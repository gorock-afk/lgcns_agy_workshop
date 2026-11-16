"""04_legacy_settlement_service.py — 엔터프라이즈 정산·환불 원장 엔진 (보안·동시성·정밀도 복합 검증 과제).

실행 방법:
    python3 04_legacy_settlement_service.py

Part 2 (CWE-bench / SEC-bench 무중단 보안 핫패치) 및 Part 3 (/boost Worker 1->2 교차 검증) 실습에 사용합니다.
현재 내장된 7개 자동 검증 테스트 중 5개가 실패합니다:
  1. [CWE-89 SQL Injection] 동적 쿼리 문자열 결합으로 인한 타 가맹점 원장 유출 취약점
  2. [특수문자 회귀 오류] "O'Connor Global" 등 아포스트로피 포함 정상 가맹점 조회 시 SQL 문법 에러 발생
  3. [TOCTOU 동시성 경쟁] 멀티스레드 동시 정산 출금 시 에스크로 잔액 음수(-) 돌파
  4. [부분 환불 누적 초과] 부분 환불 반복 요청 시 원결제 금액을 초과하여 환불되는 논리 허점
  5. [음수 환불 악용] 음수(-) 환불 금액 입력 시 가맹점 잔액이 오히려 증식되는 역방향 취약점
  6. [부동소수점 오차] float 수수료(3.3%) 누적으로 대량 정산 시 원화 1원 단위 불일치 발생
"""

from decimal import Decimal, ROUND_HALF_UP
import sqlite3
import threading
import time
import unittest
from typing import Dict, List, Tuple


class EnterpriseSettlementService:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_schema()
        self.escrow_balances: Dict[str, float] = {}
        self.processed_idem_keys: Dict[str, str] = {}

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE settlements (
                tx_id TEXT PRIMARY KEY,
                merchant_name TEXT NOT NULL,
                gross_krw INTEGER NOT NULL,
                fee_krw INTEGER NOT NULL,
                net_krw INTEGER NOT NULL,
                refunded_krw INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def seed_escrow(self, merchant_name: str, amount_krw: int) -> None:
        self.escrow_balances[merchant_name] = float(amount_krw)

    def record_settlement(self, tx_id: str, merchant_name: str, gross_krw: int) -> int:
        # 결함 A: float 수수료 계산으로 반올림 경계 오차 발생
        fee = int(gross_krw * 0.033)
        net = gross_krw - fee
        cur = self.conn.cursor()
        # 결함 B: 문자열 포맷팅 SQL 결합 -> SQL Injection 및 O'Connor 아포스트로피 크래시!
        sql = (
            f"INSERT INTO settlements (tx_id, merchant_name, gross_krw, fee_krw, net_krw, status) "
            f"VALUES ('{tx_id}', '{merchant_name}', {gross_krw}, {fee}, {net}, 'PAID')"
        )
        cur.execute(sql)
        self.conn.commit()
        return net

    def search_merchant_ledger(self, merchant_name: str) -> List[Tuple]:
        cur = self.conn.cursor()
        # 결함 B-2: Taint 경로 그대로 동적 WHERE 절 결합
        query = f"SELECT tx_id, merchant_name, gross_krw, net_krw, refunded_krw, status FROM settlements WHERE merchant_name = '{merchant_name}'"
        cur.execute(query)
        return cur.fetchall()

    def withdraw_escrow(self, merchant_name: str, payout_krw: int, idem_key: str) -> float:
        # 결함 C: 락 부재(TOCTOU) 및 멱등성 키 검증 누락
        bal = self.escrow_balances.get(merchant_name, 0.0)
        if bal < payout_krw:
            raise ValueError("Insufficient escrow balance")
        time.sleep(0.003)
        self.escrow_balances[merchant_name] = bal - payout_krw
        return self.escrow_balances[merchant_name]

    def partial_refund(self, tx_id: str, refund_krw: int) -> int:
        # 결함 D: 음수 환불 방어 누락 + 누적 환불액(refunded_krw + refund_krw) 미검증 + 락 부재
        cur = self.conn.cursor()
        cur.execute("SELECT gross_krw, refunded_krw FROM settlements WHERE tx_id = ?", (tx_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(tx_id)
        gross_krw, refunded_krw = row
        if refund_krw > gross_krw:
            raise ValueError("Refund exceeds gross amount")
        new_refunded = refunded_krw + refund_krw
        cur.execute(
            "UPDATE settlements SET refunded_krw = ?, status = 'PARTIAL_REFUND' WHERE tx_id = ?",
            (new_refunded, tx_id),
        )
        self.conn.commit()
        return new_refunded


class SettlementVerificationSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = EnterpriseSettlementService()

    def test_01_blocks_sql_injection(self) -> None:
        self.svc.record_settlement("TX-100", "AlphaCorp", 100000)
        self.svc.record_settlement("TX-101", "SecretHolding", 900000)
        leaked = self.svc.search_merchant_ledger("AlphaCorp' OR '1'='1")
        self.assertEqual(len(leaked), 0, "SQL Injection 공격 시 타 가맹점 원장이 유출되면 안 됩니다!")

    def test_02_supports_apostrophe_merchant_names(self) -> None:
        net = self.svc.record_settlement("TX-200", "O'Connor Global Logistics", 100015)
        rows = self.svc.search_merchant_ledger("O'Connor Global Logistics")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "TX-200")
        self.assertGreater(net, 0)

    def test_03_decimal_half_up_fee_precision(self) -> None:
        # 100,015 * 0.033 = 3300.495 -> 3300, 100,030 * 0.033 = 3300.99 -> 3301 (ROUND_HALF_UP)
        net_a = self.svc.record_settlement("TX-301", "PrecisionCo", 100015)
        net_b = self.svc.record_settlement("TX-302", "PrecisionCo", 100030)
        self.assertEqual(net_a, 100015 - 3300)
        self.assertEqual(net_b, 100030 - 3301)

    def test_04_concurrent_withdraw_never_goes_negative(self) -> None:
        self.svc.seed_escrow("ConcurrencyMerchant", 100000)
        errors = []

        def worker(i: int) -> None:
            try:
                self.svc.withdraw_escrow("ConcurrencyMerchant", 60000, f"idem-{i}")
            except ValueError as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertGreaterEqual(self.svc.escrow_balances["ConcurrencyMerchant"], 0)
        self.assertEqual(self.svc.escrow_balances["ConcurrencyMerchant"], 40000)
        self.assertEqual(len(errors), 5)

    def test_05_withdraw_idempotency_key_dedup(self) -> None:
        self.svc.seed_escrow("IdemMerchant", 100000)
        b1 = self.svc.withdraw_escrow("IdemMerchant", 30000, "same-idem-key-01")
        b2 = self.svc.withdraw_escrow("IdemMerchant", 30000, "same-idem-key-01")
        self.assertEqual(b1, 70000)
        self.assertEqual(b2, 70000)

    def test_06_blocks_cumulative_over_refund(self) -> None:
        self.svc.record_settlement("TX-600", "RefundCo", 50000)
        self.svc.partial_refund("TX-600", 30000)
        with self.assertRaises(ValueError):
            self.svc.partial_refund("TX-600", 25000)

    def test_07_blocks_negative_or_zero_refund(self) -> None:
        self.svc.record_settlement("TX-700", "RefundCo", 50000)
        with self.assertRaises(ValueError):
            self.svc.partial_refund("TX-700", -10000)
        with self.assertRaises(ValueError):
            self.svc.partial_refund("TX-700", 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
