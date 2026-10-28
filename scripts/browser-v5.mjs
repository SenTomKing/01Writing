// 使用专用隔离账户验证新增续写入口；凭证由测试夹具提供，不输出。
import {chromium} from '../frontend/node_modules/@playwright/test/index.mjs';
import {readFile,mkdir} from 'node:fs/promises';
const input=JSON.parse(await readFile(process.env.WRITING_BROWSER_FIXTURE,'utf8'));
const browser=await chromium.launch({headless:true,channel:process.env.WRITING_BROWSER_CHANNEL||undefined});
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(process.env.WRITING_WEB_URL||'http://127.0.0.1:5173');
 await page.getByLabel('用户名').fill(input.username);await page.getByLabel('密码').fill(input.password);
 await page.getByRole('button',{name:'登录工作台',exact:true}).click();
 await page.getByRole('heading',{name:'我的文档'}).waitFor();
 await page.getByText('我的运行统计 · 最近 30 天',{exact:true}).click();
 await page.getByText('补充信息全流程验收',{exact:true}).click();
 await page.getByLabel('补充说明').fill('根据正式说明，只删除啊，不修改其他内容。');
 await page.getByRole('checkbox',{name:'资料 · 补充正式说明',exact:true}).check();
 await page.getByRole('button',{name:'使用补充信息继续',exact:true}).click();
 await page.getByRole('button',{name:'接受并更新正文',exact:true}).waitFor({timeout:60000});
 await page.reload();await page.getByRole('button',{name:'接受并更新正文',exact:true}).waitFor();
 await page.getByText('任务耗时与用量',{exact:true}).click();
 await page.getByRole('button',{name:'接受并更新正文',exact:true}).click();
 await page.getByText('正文版本 2',{exact:true}).waitFor();
 if(await page.getByRole('textbox',{name:'正文',exact:true}).inputValue()!=='这个记录很好。')throw Error('正文不正确');
 await page.getByRole('button',{name:'查看上一任务及原始依据',exact:true}).click();
 await page.getByLabel('补充说明').waitFor();
 await mkdir('outputs',{recursive:true});await page.screenshot({path:'outputs/v5-clarification.png',fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('v5网页：登录、统计、选择新资料、补充、刷新、接受、旧任务回看全部通过');
}finally{await browser.close();}
