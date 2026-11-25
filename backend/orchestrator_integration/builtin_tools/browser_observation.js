'use strict';

// Observe the actual browser operation. Do not depend on console.log wording or
// change requests to make a failing application appear to pass.
function valuesFromBody(value, found = [], depth = 0) {
  if (!value || depth > 9 || found.length >= 60) return found;
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 30)) valuesFromBody(item, found, depth + 1);
  } else if (typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      if (/^(text|value|conf_value|name|color)$/i.test(key) && typeof item === 'string' && item.length <= 160) {
        found.push(item);
      } else if (item && typeof item === 'object') valuesFromBody(item, found, depth + 1);
    }
  }
  return [...new Set(found)];
}

function isCartUrl(url) {
  return /\/carts?(?:\/|$|\?)/i.test(url);
}

function summarizeResponse(url, method, status, body, requestBody) {
  const data = body && typeof body.data === 'object' && body.data || {};
  const error = body?.error;
  const rejected = status >= 400 || body?.success === false || (error && body?.success !== true);
  const identifiers = {};
  const visit = (node, depth = 0) => {
    if (!node || typeof node !== 'object' || depth > 6) return;
    for (const [key, value] of Object.entries(node)) {
      if (/^(cart_id|cart_item_id|product_id|order_sn|order_id)$/i.test(key) && value != null) identifiers[key] = value;
      else if (value && typeof value === 'object') visit(value, depth + 1);
    }
  };
  visit(data);
  let path = url;
  try { path = new URL(url).origin + new URL(url).pathname; } catch (_) {}
  return {
    url: path, method, http_status: status, action: isCartUrl(url) ? 'cart' : 'request',
    success: rejected ? false : (body?.success === true ? true : null),
    retryable: rejected ? (status === 429 || status >= 500) : false,
    error_code: String(error?.status || error?.code || (rejected ? status : '')),
    message: String(error?.message || body?.message || body?.msg || '').slice(0, 600),
    request_values: valuesFromBody(requestBody), identifiers,
    request_product_id: requestBody?.product_id ?? requestBody?.id ?? null,
    request_quantity: requestBody?.num ?? requestBody?.quantity ?? null,
    ...(isCartUrl(url) && method === 'GET' && Array.isArray(data.cart) ? {
      cart_count: data.cart.length, select_quantity: data.settlement?.select_quantity,
      items_amount: data.settlement?.items,
      cart_items: data.cart.map(item => ({ values: valuesFromBody(item),
        quantity: item.num ?? item.quantity ?? item.qty,
        product_id: item.product_id, cart_id: item.cart_id, cart_item_id: item.cart_item_id })),
    } : {}),
  };
}

async function startObservation(context, page) {
  const responses = [], pending = [];
  const screenshots = [];
  const trackedPage = page;
  const installValidationObserver = () => {
    window.__skillValidationObserver?.disconnect();
    window.__skillValidationErrors = [];
    const scan = () => {
      for (const el of document.querySelectorAll('.text-error,[aria-invalid="true"]')) {
        if (!el.offsetParent) continue;
        const field = el.matches('input,textarea') ? el : (el.closest('.attribute-item,.el-form-item,[data-field]') || el.parentElement)?.querySelector('input,textarea');
        const message = (el.innerText || el.validationMessage || '').trim();
        if (field && field.value === '' && message && !window.__skillValidationErrors.includes(message)) window.__skillValidationErrors.push(message);
      }
    };
    window.__skillValidationObserver = new MutationObserver(scan);
    window.__skillValidationObserver.observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class', 'aria-invalid'] });
    scan();
  };
  if (page) {
    await page.addInitScript(installValidationObserver);
    await page.evaluate(installValidationObserver).catch(() => {});
  }
  const originalScreenshot = page?.screenshot;
  if (originalScreenshot) {
    page.screenshot = async function(options) {
      const output = await originalScreenshot.call(this, options);
      if (options?.path) screenshots.push(String(options.path));
      return output;
    };
  }
  let writesStarted = 0;
  const onRequest = request => {
    if (isCartUrl(request.url()) && /^(POST|PUT|PATCH|DELETE)$/.test(request.method())) writesStarted++;
  };
  const onResponse = response => {
    const request = response.request();
    const method = request.method(), url = response.url();
    if (request.resourceType() === 'document') {
      responses.push({ url, method, http_status: response.status(), action: 'navigation' });
      return;
    }
    if (!isCartUrl(url) && !/^(POST|PUT|PATCH|DELETE)$/.test(method)) return;
    pending.push((async () => {
      let body, requestBody;
      try { body = await response.json(); } catch (_) { return; }
      try { requestBody = request.postDataJSON(); } catch (_) {}
      const summary = summarizeResponse(url, method, response.status(), body, requestBody);
      // Capture each row before the caller can move to the next parameter row.
      // The end-of-script DOM belongs only to the last row of a batch.
      try {
        summary.previews = await request.frame().evaluate(() => [...document.querySelectorAll('.text-preview,[data-testid="preview"]')].map(el => (el.innerText || '').trim().slice(0, 200)));
      } catch (_) {}
      responses.push(summary);
    })().catch(() => {}));
  };
  context.on('request', onRequest);
  context.on('response', onResponse);
  return async page => {
    if (originalScreenshot && trackedPage) trackedPage.screenshot = originalScreenshot;
    context.off('request', onRequest);
    context.off('response', onResponse);
    await Promise.allSettled(pending);
    const observation = { responses, screenshots, cart_writes_started: writesStarted, page_url: page?.url?.() || '' };
    try {
      Object.assign(observation, await page.evaluate(() => ({
        title: document.title, ready: document.readyState,
        input_values: [...document.querySelectorAll('input,textarea')]
          .filter(el => el.offsetParent && !/password|email|hidden/.test(el.type || '') && !/token|secret|card|captcha/i.test(el.name || ''))
          .map(el => String(el.value || '').slice(0, 160)).slice(0, 40),
        validation_errors: [...document.querySelectorAll('.text-error,[role="alert"],[aria-invalid="true"]')]
          .filter(el => el.offsetParent).map(el => (el.innerText || el.validationMessage || '').trim())
          .filter(Boolean).slice(0, 8),
        empty_validation_blocked: [...document.querySelectorAll('.text-error,[aria-invalid="true"]')].some(el => {
          if (!el.offsetParent) return false;
          const field = el.matches('input,textarea') ? el : (el.closest('.attribute-item,.el-form-item,[data-field]') || el.parentElement)?.querySelector('input,textarea');
          return field && field.value === '' && (el.innerText.trim() || el.getAttribute('aria-invalid') === 'true');
        }),
        previews: [...document.querySelectorAll('.text-preview,[data-testid="preview"]')]
          .map(el => (el.innerText || '').trim().slice(0, 200)).slice(0, 8),
        empty_validation_errors: window.__skillValidationErrors || [],
      })));
    } catch (_) {}
    return observation;
  };
}

module.exports = { startObservation, summarizeResponse, valuesFromBody, isCartUrl };
