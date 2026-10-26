// CI 的关键用户流程；可对同源网页或 Vite 代理运行。
import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';
import { randomBytes } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const browser = await chromium.launch({headless:true,channel:process.env.WRITING_BROWSER_CHANNEL||undefined});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const username='smoke_'+randomBytes(5).toString('hex');
const password=randomBytes(20).toString('hex');
try {
  await page.goto(process.env.WRITING_WEB_URL||'http://127.0.0.1:5173');
  await page.getByRole('button',{name:'创建账户',exact:true}).click();
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button',{name:'创建账户',exact:true}).click();
  await page.getByRole('heading',{name:'我的文档'}).waitFor();
  await page.getByRole('button',{name:'新建文档'}).click();
  await page.getByLabel('文档标题').fill('端到端验证文档');
  await page.getByLabel('初始正文').fill('实验结果为12.4%，这个结果很好啊。');
  await page.getByRole('button',{name:'创建文档',exact:true}).click();
  await page.getByRole('button',{name:'生成修改建议'}).click();
  await page.getByRole('button',{name:'接受并更新正文'}).waitFor({timeout:120000});
  await page.getByLabel('拒绝原因').fill('只删除啊，保留数字和语气');
  await page.getByRole('button',{name:'拒绝并保存原因'}).click();
  await page.getByRole('button',{name:'根据反馈重新生成'}).click();
  await page.getByRole('button',{name:'接受并更新正文'}).waitFor({timeout:120000});
  await page.reload();
  await page.getByRole('button',{name:'接受并更新正文'}).waitFor();
  await page.getByRole('button',{name:'接受并更新正文'}).click();
  await page.getByText('正文版本 2',{exact:true}).waitFor();
  if((await page.getByRole('textbox',{name:'正文',exact:true}).inputValue()).includes('啊'))throw new Error('接受后正文未更新');
  await page.getByText('版本 1',{exact:true}).click();
  await page.getByRole('button',{name:'将此内容恢复为新版本'}).click();
  await page.getByText('正文版本 3',{exact:true}).waitFor();
  const restored=await page.getByRole('textbox',{name:'正文',exact:true}).inputValue();
  if(restored!=='实验结果为12.4%，这个结果很好啊。')throw new Error('历史恢复未保留原文');
  // 实际下载并核对两种导出，避免只验证链接存在。
  for(const [label,extension] of [['导出 Markdown','.md'],['导出纯文本','.txt']]) {
    const pending=page.waitForEvent('download');
    await page.getByRole('link',{name:label}).click();
    const download=await pending;
    if(await download.failure())throw new Error(label+'下载失败');
    if(!download.suggestedFilename().endsWith(extension))throw new Error(label+'扩展名错误');
    const content=await readFile(await download.path(),'utf8');
    if(!content.includes(restored))throw new Error(label+'未包含当前正文');
  }
  const screenshot=process.env.WRITING_SCREENSHOT||fileURLToPath(new URL('../outputs/workbench.png',import.meta.url));
  await mkdir(fileURLToPath(new URL('../outputs/',import.meta.url)),{recursive:true});
  await page.screenshot({path:screenshot,fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  await page.getByRole('button',{name:'退出',exact:true}).click();
  await page.getByRole('heading',{name:'欢迎回来'}).waitFor();
  await page.goto(process.env.WRITING_WEB_URL||'http://127.0.0.1:5173');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('heading',{name:'我的文档'}).waitFor();
  await page.getByText('端到端验证文档',{exact:true}).click();
  await page.getByText('正文版本 3',{exact:true}).waitFor();
  if(await page.getByRole('textbox',{name:'正文',exact:true}).inputValue()!==restored)throw new Error('重新登录后正文不一致');
  await page.getByRole('button',{name:'退出',exact:true}).click();
  await page.getByRole('heading',{name:'欢迎回来'}).waitFor();
  if(errors.length)throw new Error(errors.join('\n'));
  console.log('浏览器流程通过：注册、新建、生成、拒绝反馈、重试、刷新恢复、接受、历史恢复、MD/TXT实际下载、退出与重新登录。');
} finally {await browser.close();}
