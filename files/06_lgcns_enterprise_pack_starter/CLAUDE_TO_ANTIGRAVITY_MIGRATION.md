# Claude Code (.claude/) ➔ Google Antigravity (.agents/) 1:1 마이그레이션 치트시트

| 커스터마이징 체계 | 기존 Claude Code 경로 | Google Antigravity 경로 | 호환 및 변환 핵심 포인트 |
| :--- | :--- | :--- | :--- |
| **전역/루트 지침** | `~/.claude/CLAUDE.md`, `./CLAUDE.md` | `~/.gemini/GEMINI.md`, `./AGENTS.md` | 마크다운 본문 100% 그대로 호환 (`AGENTS.md` 권장) |
| **모듈형 Rules** | `.claude/rules/*.md` | `.agents/rules/*.md` | 상단 YAML 프론트매터에 `trigger: always_on` / `glob` / `model_decision` 명시 |
| **업무 절차 Skills** | `.claude/skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | Agent Skills 표준 동일 (`name`, `description` 유지, `/스킬명` 자동 매핑) |
| **MCP 서버 연동** | `.mcp.json` | `.agents/mcp_config.json` | `stdio` 블록 100% 호환, 원격 HTTP 서버는 `"serverUrl": "..."` 키 사용 |
| **커스텀 Agents** | `.claude/agents/*.md` | `.agents/agents/*.md` | 프롬프트 본문 동일, 읽기 전용 감사는 `enableWriteTools: false` 지정 |
| **결정론적 Hooks** | `.claude/settings.json` (`hooks`) | `.agents/hooks.json` | 훅 이름 최상위 키로 감싸고 기존 `.sh` / `.py` 검증 스크립트 그대로 재사용 |
