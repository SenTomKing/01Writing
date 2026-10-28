from pathlib import Path
import json,secrets,time
from fastapi.testclient import TestClient
from writing_server.settings import Settings
from writing_server.app import create_app
settings=Settings.load()
assert settings.provider=='demo','此脚本仅可用于独立演示验收环境'
app=create_app(settings);username='clarify_'+str(int(time.time()));password=secrets.token_urlsafe(24)
with TestClient(app) as c:
 me=c.post('/api/v1/auth/register',json={'username':username,'password':password}).json();c.headers['X-CSRF-Token']=me['csrf_token']
 doc=c.post('/api/v1/documents',json={'title':'补充信息全流程验收','content':'这个记录很好啊。'}).json()
 # 使用后台服务相同的持久化路径，先制造缺口；网页执行新任务仍走正常运行器。
 from writing_server import services
 with app.state.database.sessions.begin() as session:
  task=services.create_task(session,me['id'],doc['id'],'按补充说明仅删除啊',doc['current_version_id'],'seed-clarify',settings)
  task.status='failed';task.error_code='evidence_needed';task.error_message='需要补充正式说明'
  task_id=task.id
 source=c.post('/api/v1/knowledge',json={'kind':'source','title':'补充正式说明','content':'保留其他内容，只删除啊。'}).json()
 folder=Path('private/v5-qa');folder.mkdir(parents=True,exist_ok=True)
 (folder/'browser.json').write_text(json.dumps({'username':username,'password':password,'doc':doc['id'],'task':task_id,'source':source['id']}),encoding='utf-8')
print('clarification browser fixture created')
