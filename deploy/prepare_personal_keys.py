"""为现有部署增加加密主密钥，只追加缺失配置，不打印密钥或覆盖已有主密钥。"""
import base64
from pathlib import Path
import secrets
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
target = root / '.env.deploy'
if not target.is_file():
    raise SystemExit('请先运行 deploy/prepare_env.py')
values = dotenv_values(target)
if values.get('WRITING_CREDENTIAL_MASTER_KEY'):
    raise SystemExit('主密钥已配置，未作修改。请保留独立备份。')
if 'WRITING_CREDENTIAL_MASTER_KEY' in values:
    raise SystemExit('文件存在空的主密钥项，请先移除该空行再运行，避免重复配置')
with target.open('a', encoding='utf-8') as handle:
    handle.write('\nWRITING_CREDENTIAL_MASTER_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + '\n')
print('已生成主密钥。请安全备份 .env.deploy；请勿提交、截图或公开其内容。')
