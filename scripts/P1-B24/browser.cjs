const http = require('http');
const {chromium} = require('playwright');
const fs = require('fs');
(async()=>{
 const fixture=JSON.parse(fs.readFileSync('/workspace/ops/test-output/P1-B24/fixture.json'));
 const proxy=http.createServer((req,res)=>{const target=http.request({hostname:'jrc-proxy',port:8080,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});target.on('error',()=>res.destroy());req.pipe(target);}); await new Promise(r=>proxy.listen(13663,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage','--js-flags=--max-old-space-size=2048']});
 try {
 const context=await browser.newContext({baseURL:'http://127.0.0.1:13663'});
 const page=await context.newPage(); page.setDefaultTimeout(15000);
 await page.goto('/jrc/members/invite/'+fixture.token+'/accept/');
 await page.getByRole('link',{name:'Sign Up to Join'}).click();
 if(await page.locator('[name=email]').inputValue() !== fixture.email) throw Error('Wrong invite address');
 await page.locator('[name=password1]').fill(fixture.password);
 await page.getByRole('button',{name:'Create Account'}).click();
 await page.waitForLoadState('networkidle');
 const expired=await page.getByText('Invitation Expired',{exact:true}).count();
 await page.screenshot({path:'/workspace/ops/screenshoty/P1-B24/'+(process.env.PHASE||'before')+'-signup.png',fullPage:true});
 console.log(JSON.stringify({phase:process.env.PHASE||'before',url:page.url(),expired:!!expired,title:await page.title()}));
 if(process.env.PHASE==='after' && expired) throw Error('Signup still lands on expired invitation');
 if(process.env.PHASE==='after' && !page.url().endsWith('/calendar/')) throw Error('Signup did not reach assigned workspace');
 } finally {await browser.close();proxy.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
