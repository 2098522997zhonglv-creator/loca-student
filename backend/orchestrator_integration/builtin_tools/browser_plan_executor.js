'use strict';

// Reusable form executor. Bindings come from the current DOM, never a product
// URL/ID or a selector guessed by the model. Unknown widgets return a snapshot
// for repair; already observed writes are not retried here.
const path = require('path');
const { valuesFromBody, isCartUrl } = require('./browser_observation');

async function discover(page) {
  return page.evaluate(() => {
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const norm = text => (text || '').replace(/\s+/g, ' ').trim();
    const mark = (name, el) => {
      if (!el) return null;
      document.querySelectorAll(`[data-loca-plan="${name}"]`).forEach(x => x.removeAttribute('data-loca-plan'));
      el.setAttribute('data-loca-plan', name);
      return `[data-loca-plan="${name}"]`;
    };
    const groupFor = el => el.closest('.attribute-item,.el-form-item,fieldset,[data-field],[role="group"]');
    const inputs = [...document.querySelectorAll('input,textarea')].filter(el => visible(el) && !el.disabled && !el.readOnly);
    const textFields = inputs.filter(el => /^(text|textarea)$/.test(el.type || el.tagName.toLowerCase()) && !/search|quantity|qty/i.test([el.name, el.className, el.placeholder].join(' ')));
    const labelled = textFields.filter(el => /(?:\btext\b|文本|文字)/i.test(norm(groupFor(el)?.innerText || el.labels?.[0]?.innerText)));
    const text = labelled.length === 1 ? labelled[0] : textFields.length === 1 ? textFields[0] : null;
    const quantity = [...document.querySelectorAll('input')].filter(el=>visible(el)&&!el.disabled).find(el => /qty|quantity/i.test([el.name, el.className, el.id].join(' ')) || el.type === 'number');
    const submits = [...document.querySelectorAll('button,a,div,span,[role="button"]')].filter(el => visible(el) && /^(?:add\s+to\s+(?:cart|bag)|加入购物车|加入購物車)$/i.test(norm(el.innerText)));
    const submit = submits.find(el => el.matches('button,[role="button"]') || /add[-_]?cart/i.test(el.className || '')) || submits[0];
    const cart = [...document.querySelectorAll('a[href]')].find(el => {
      try { const url = new URL(el.href); return visible(el) && url.origin === location.origin && /\/(?:cart|basket)(?:\/index)?\/?$/.test(url.pathname); } catch (_) { return false; }
    });
    const cartControls=[...document.querySelectorAll('button,[role="button"],a,div')].filter(el=>visible(el) &&
      (/^(?:shopping\s*)?cart$/i.test(el.getAttribute('aria-label') || el.title || '') || /(?:^|\s)cart[-_](?:btn|button)(?:\s|$)/i.test(el.className || '')));
    const cartControl=cartControls.length===1 ? cartControls[0] : null;
    const groups = [...document.querySelectorAll('.attribute-item,fieldset,[data-field]')].filter(visible).map((el,i) => ({
      selector:mark('group-'+i,el),
      label: norm(el.innerText).slice(0, 250),
      displayed_value:norm(el.innerText).split('*').slice(1).join('*').trim(),
      hasText: !!el.querySelector('input:not([type="hidden"]),textarea'),
      options: [...new Set([...el.querySelectorAll('option,[role="option"],[role="radio"],[class*="select-item"]')].filter(visible).map(x => norm(x.innerText)).filter(Boolean))].slice(0, 40),
    })).slice(0, 15);
    return { text: mark('text', text), quantity: mark('quantity', quantity), submit: mark('submit', submit),
      cart_url: cart?.href || '', cart_control:mark('cart',cartControl), quantity_readonly:!!quantity?.readOnly, groups, text_candidates: textFields.map(el => ({name:el.name, placeholder:el.placeholder, label:norm(groupFor(el)?.innerText).slice(0,120)})) };
  });
}

async function selectExact(page, value, scope = null) {
  const binding = await page.evaluate(({value,scope}) => {
    const root = scope ? document.querySelector(scope) : document;
    if(!root) return {error:"选项组已变化，需要重新绑定"};
    const norm = t => (t || '').replace(/\s+/g, ' ').trim();
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const native = [...root.querySelectorAll('select')].filter(visible).find(el => [...el.options].some(o => norm(o.label) === value));
    let target = native;
    if (!target) {
      const candidates = [...root.querySelectorAll('button,label,div,span,li,[role="option"],[role="radio"]')]
        .filter(el => visible(el) && norm(el.innerText) === value);
      const resolve = el => {
        let target = el;
        for (let p=el; p && p!==document.body && norm(p.innerText)===value; p=p.parentElement) {
          if (p.matches('button,label,[role="option"],[role="radio"]')) return p;
          if (/select-item|option|tile/i.test(p.className || '')) target=p;
        }
        return target;
      };
      const targets = [...new Set(candidates.map(resolve))];
      if (targets.length !== 1) return {error:`选项 ${value} 需要明确绑定，匹配 ${targets.length} 个节点`};
      // Click the actual label/content, not merely its containing tile.
      // Vue/custom controls often attach the handler to an inner element;
      // clicking its ancestor never dispatches that child's handler.
      target = candidates.find(el => resolve(el)===targets[0] && ![...el.children].some(child=>visible(child)&&norm(child.innerText)===value)) || targets[0];
    }
    document.querySelectorAll('[data-loca-plan="choice"]').forEach(x => x.removeAttribute('data-loca-plan'));
    target.setAttribute('data-loca-plan','choice');
    return {selector:'[data-loca-plan="choice"]', native:target.tagName==='SELECT'};
  }, {value,scope});
  if (binding.error) throw new Error(binding.error);
  if (binding.native) await page.selectOption(binding.selector, {label:value});
  else await page.locator(binding.selector).click();
}

async function screenshot(page, plan, name) {
  if (plan.screenshots) await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, name + '.png'),fullPage:false});
}

async function requiredCheck(page, bindings) {
  const context = page.context();
  let requests = 0;
  const count = req => { if(isCartUrl(req.url()) && /POST|PUT|PATCH/.test(req.method())) requests++; };
  context.on('request', count);
  let resolveResponse;
  const response = new Promise(resolve => { resolveResponse = resolve; });
  const onResponse = res => { if(isCartUrl(res.url()) && /POST|PUT|PATCH/.test(res.request().method())) resolveResponse({passed:false, errors:[`空文本触发加购 HTTP ${res.status()}`]}); };
  context.on('response', onResponse);
  try {
    await page.locator(bindings.text).fill('');
    Object.assign(bindings,await discover(page));
    await page.locator(bindings.submit).click();
    const validation = page.waitForFunction(() => [...document.querySelectorAll('.text-error,[role="alert"],[aria-invalid="true"]')]
      .filter(el => el.getClientRects().length).map(el => (el.innerText || el.validationMessage || '').trim()).filter(Boolean).length > 0,
      null, {timeout:8000}).then(async handle => {
        await handle.dispose();
        return {passed:requests===0, errors:await page.locator('.text-error,[role="alert"],[aria-invalid="true"]').allTextContents()};
      }).catch(() => ({passed:false, errors:['未观察到空文本必填拦截']}));
    return await Promise.race([validation, response]);
  } finally { context.off('request', count); context.off('response', onResponse); }
}

async function execute(page, plan) {
  const started = Date.now();
  const result = {action:plan.action, status:'needs_binding', rows:[], steps:[], duration_ms:0};
  try {
    if (plan.action === 'inspect_form') {
      await page.goto(plan.url,{waitUntil:'domcontentloaded'});
      await page.waitForFunction(() => [...document.querySelectorAll('input,textarea')].some(el => el.getClientRects().length && !el.readOnly && /text|textarea/.test(el.type || el.tagName.toLowerCase()))
        && [...document.querySelectorAll('button,a,div,span')].some(el => /^(add\s+to\s+(cart|bag)|加入购物车)$/i.test((el.innerText || '').trim())), null,{timeout:20000});
      result.bindings = await discover(page);
      await screenshot(page,plan,'01_plan_page');
      result.status = result.bindings.text && result.bindings.submit && (result.bindings.cart_url || result.bindings.cart_control) ? 'ready' : 'needs_binding';
      return result;
    }
    if (plan.action !== 'run_form_test') throw new Error('不支持的执行计划动作');
    const target = new URL(plan.url), current = new URL(page.url());
    if (current.origin!==target.origin || current.pathname!==target.pathname) throw new Error('当前页面与计划目标不同，需修复当前节点');
    // DOM bindings belong to the executor. Never ask an LLM to round-trip
    // selectors/options/readonly metadata: a shortened copy is not a binding.
    const bindings = await discover(page);
    if (!bindings?.text || !bindings?.submit || (!bindings?.cart_url && !bindings?.cart_control)) throw new Error('计划缺少已识别表单绑定');
    // Preserve explicitly requested no-extra defaults. Unknown additional
    // mandatory groups are returned for binding, not silently omitted.
    if (plan.no_extras) {
      const unknown = (bindings.groups || []).filter(g => !g.hasText && !plan.rows.some(r => g.options.includes(r.color)) && !/no,?\s*thanks|不需要|无需|不要/i.test(g.label));
      if(unknown.length) throw new Error('额外属性需要绑定：'+unknown.map(g=>g.label).join('；'));
      for(const group of bindings.groups || []) {
        const none=[...new Set(group.options || [])].filter(x=>/^no,?\s*thanks\.?$|^(?:不需要|无需|不要)$/i.test(x));
        if(none.length===1 && group.selector) await selectExact(page,none[0],group.selector);
        else if(none.length===0 && /^no,?\s*thanks\.?$|^(?:不需要|无需|不要)$/i.test(group.displayed_value || '')) continue;
        else if(/no,?\s*thanks|不需要|无需|不要/i.test(group.label) && !group.hasText) throw new Error('无法确认不添加额外选项，需要绑定');
      }
    }
    Object.assign(bindings,await discover(page));
    if(plan.empty_check) {
      if(plan.rows[0]?.color) await selectExact(page,plan.rows[0].color);
      Object.assign(bindings,await discover(page));
      result.empty_check = await requiredCheck(page,bindings);
      result.steps.push({node_id:'empty_check',...result.empty_check});
      await screenshot(page,plan,'02_plan_empty_check');
    }
    for(const row of plan.rows) {
      const record = {key:row.key, value:row.value, color:row.color, quantity:row.quantity};
      try {
        if(row.color) await selectExact(page,row.color);
        Object.assign(bindings,await discover(page));
        await page.locator(bindings.text).fill(row.value);
        Object.assign(bindings,await discover(page));
        if(bindings.quantity) {
          const quantity=page.locator(bindings.quantity), expected=String(row.quantity || 1);
          if(await quantity.inputValue()!==expected) {
            if(bindings.quantity_readonly) throw new Error('数量下拉需绑定目标选项');
            await quantity.fill(expected);
          }
        }
        else if(row.quantity && row.quantity!==1) throw new Error('数量控件需要明确绑定');
        await page.waitForFunction(value => [...document.querySelectorAll('.text-preview,[data-testid="preview"],[class*="preview"]')]
          .some(el => el.getClientRects().length && (el.innerText || '').includes(value)),row.value,{timeout:10000});
        await screenshot(page,plan,`${row.key}_preview`);
        const response = page.waitForResponse(res => isCartUrl(res.url()) && /POST|PUT|PATCH/.test(res.request().method()),{timeout:15000});
        // Consume a possible timeout immediately; an uncertain write must not
        // turn into an unhandled rejection or an automatic retry.
        response.catch(()=>{});
        await page.locator(bindings.submit).click();
        const res = await response;
        let body; try {body=await res.json();} catch (_) {body=null;}
        record.http_status=res.status(); record.success=res.ok() && body?.success!==false;
        record.retryable=res.status()===429 || res.status()>=500;
        record.response_url=res.url();
        let sent; try {sent=res.request().postDataJSON();} catch (_) {}
        const values=valuesFromBody(sent);
        if(!values.includes(row.value) || (row.color&&!values.includes(row.color)) || String(sent?.num ?? sent?.quantity)!==String(row.quantity || 1)) {
          record.parameter_mismatch=true; result.status='needs_binding';
        }
        await screenshot(page,plan,`${row.key}_result`);
      } catch(error) {
        record.error=String(error.message || error); record.status='unknown';
        // Remaining rows need model repair if a write may be in flight.
        result.rows.push(record); result.message=record.error;
        return result;
      }
      result.rows.push(record);
    }
    Object.assign(bindings,await discover(page));
    if(bindings.cart_url) await page.goto(bindings.cart_url,{waitUntil:'domcontentloaded'});
    else {
      await page.locator(bindings.cart_control).click();
      await page.waitForFunction(() => /\/(?:cart|basket)(?:\/index)?\/?$/.test(location.pathname) ||
        [...document.querySelectorAll('h1,h2,h3,[role="dialog"],[class*="cart"],[class*="drawer"]')].some(el=>el.getClientRects().length &&
          /shopping cart|your cart|my cart|view cart|cart is empty|购物车为空/i.test(el.innerText || '')),null,{timeout:15000});
    }
    const cartEndpoint=result.rows.find(r=>r.response_url)?.response_url;
    if(cartEndpoint) {
      // Read only the endpoint observed from the actual UI write, using the
      // same browser credentials. No invented endpoint or patched payload.
      result.cart_readback=await page.evaluate(async url => {
        const r=await fetch(url,{credentials:'include'});return {http_status:r.status,body:await r.json()};
      },cartEndpoint);
    }
    if(result.rows.every(r=>r.success===false)) await page.waitForFunction(() => /(?:your\s+)?cart\s+is\s+empty|购物车为空/i.test(document.body.innerText),null,{timeout:15000});
    else await page.waitForFunction(()=>document.readyState==='complete',null,{timeout:15000});
    await screenshot(page,plan,'03_plan_cart');
    result.status=result.rows.some(r=>r.parameter_mismatch||r.retryable) ? 'needs_binding' : 'completed';
    return result;
  } catch(error) {
    result.message=String(error.message || error);
    result.snapshot=await discover(page).catch(()=>null);
    return result;
  } finally {result.duration_ms=Date.now()-started;}
}

module.exports={execute,discover,selectExact};
