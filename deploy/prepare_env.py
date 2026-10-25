"""生成独立容器环境配置；不覆盖现有文件，不输出凭证。"""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / '.env.deploy'
content = f'''WRITING_DB_PASSWORD={secrets.token_urlsafe(32)}
WRITING_DB_MIGRATION_PASSWORD={secrets.token_urlsafe(32)}
WRITING_PROVIDER=demo
WRITING_ORIGINS=http://localhost:8080
WRITING_SECURE_COOKIE=false
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
'''
with path.open('x', encoding='utf-8') as handle:
    handle.write(content)
print('已生成 .env.deploy，可运行 docker compose --env-file .env.deploy up --build -d')
