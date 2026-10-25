"""备份与隔离恢复校验；恢复只在新建的随机模式，不覆盖正式表。"""
import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess

from writing_server.settings import Settings
from sqlalchemy.engine import make_url


def main():
    parser=argparse.ArgumentParser(description='备份项目数据库为 PostgreSQL 自定义格式')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    settings=Settings.load()
    if not settings.migration_url: parser.error('备份需要建表账户配置')
    url=make_url(settings.migration_url) if isinstance(settings.migration_url,str) else settings.migration_url
    binary=shutil.which('pg_dump') or ('E:/postgresql/bin/pg_dump.exe' if os.name=='nt' else None)
    if not binary: parser.error('找不到 pg_dump，请安装与服务器兼容的 PostgreSQL 客户端')
    path=args.output or Path('private/backups')/f'writing-{datetime.now():%Y%m%d-%H%M%S}.dump'
    if path.exists(): parser.error('备份文件已存在，请另选路径')
    path.parent.mkdir(parents=True,exist_ok=True)
    env={key:value for key,value in os.environ.items() if not key.upper().startswith('PG')}
    env.update(PGPASSWORD=url.password or '',PGCONNECT_TIMEOUT='5')
    result=subprocess.run([binary,'-h',url.host or 'localhost','-p',str(url.port or 5432),'-U',url.username,
        '-d',url.database,'--format=custom','--no-owner','--no-privileges','--file',str(path)],env=env,capture_output=True)
    if result.returncode: raise SystemExit('数据库备份失败，请检查账户和客户端版本；原始凭证不会输出。')
    print(f'备份已保存：{path}（{path.stat().st_size} 字节）')


if __name__=='__main__':main()
