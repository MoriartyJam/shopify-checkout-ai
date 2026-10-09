const DEBUG_URL = process.env.CHROME_DEBUG_URL ?? 'http://127.0.0.1:9222';

async function storefrontTarget() {
  const targets = await fetch(`${DEBUG_URL}/json`).then((response) => response.json());
  return targets.find((target) =>
    target.type === 'page' &&
    target.url.startsWith('https://quickstart-da63505a.myshopify.com/')
  );
}

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.sequence = 0;
    this.pending = new Map();
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, {once: true});
      this.socket.addEventListener('error', reject, {once: true});
    });
    this.socket.addEventListener('message', ({data}) => {
      const message = JSON.parse(data);
      if (!message.id || !this.pending.has(message.id)) return;
      const {resolve, reject} = this.pending.get(message.id);
      this.pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    });
  }

  call(method, params = {}) {
    const id = ++this.sequence;
    this.socket.send(JSON.stringify({id, method, params}));
    return new Promise((resolve, reject) => this.pending.set(id, {resolve, reject}));
  }

  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }

  close() {
    this.socket.close();
  }
}

const target = await storefrontTarget();
if (!target) throw new Error('Shopify storefront tab was not found');

const cdp = new CdpClient(target.webSocketDebuggerUrl);
await cdp.connect();
if (process.env.SHOW_COOKIES === '1') {
  const {cookies} = await cdp.call('Network.getAllCookies');
  console.log(JSON.stringify(cookies.map(({name, domain, path}) => ({name, domain, path})), null, 2));
}
if (process.env.ACTION === 'reset_checkout_session') {
  const {cookies} = await cdp.call('Network.getAllCookies');
  const removableNames = new Set([
    'cart', 'cart_currency', 'cart_sig', 'cart_ts', 'cart_ver',
    'checkout', 'checkout_session_lookup', 'previous_step', 'step',
  ]);
  for (const cookie of cookies) {
    if (!cookie.domain.endsWith('myshopify.com') || !removableNames.has(cookie.name)) continue;
    await cdp.call('Network.deleteCookies', {
      name: cookie.name,
      domain: cookie.domain,
      path: cookie.path,
    });
  }
  await cdp.call('Page.navigate', {url: 'https://quickstart-da63505a.myshopify.com/'});
  await new Promise((resolve) => setTimeout(resolve, 2500));
}
if (process.env.ACTION === 'reload') {
  await cdp.call('Page.reload', {ignoreCache: true});
  await new Promise((resolve) => setTimeout(resolve, 7000));
}
if (process.env.NAVIGATE_URL) {
  await cdp.call('Page.navigate', {url: process.env.NAVIGATE_URL});
  await new Promise((resolve) => setTimeout(resolve, 2500));
}
if (process.env.ACTION === 'clear_cart') {
  const cleared = await cdp.evaluate(`fetch('/cart/clear.js', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
  }).then((response) => response.ok)`);
  if (!cleared) throw new Error('Cart could not be cleared');
  await new Promise((resolve) => setTimeout(resolve, 1500));
}
if (process.env.ACTION === 'add_to_cart') {
  const clicked = await cdp.evaluate(`(() => {
    const buttons = [...document.querySelectorAll('button[type="submit"], input[type="submit"]')];
    const button = buttons.find((item) => !item.disabled &&
      ((item.textContent || item.value || '').trim().toLowerCase() === 'add to cart'));
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!clicked) throw new Error('Enabled Add to cart button was not found');
  await new Promise((resolve) => setTimeout(resolve, 2500));
}
if (process.env.ACTION === 'checkout') {
  const clicked = await cdp.evaluate(`(() => {
    const buttons = [...document.querySelectorAll('button, input[type="submit"]')];
    const button = buttons.find((item) => !item.disabled &&
      ['check out', 'checkout'].includes((item.textContent || item.value || '').trim().toLowerCase()));
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!clicked) throw new Error('Enabled checkout button was not found');
  await new Promise((resolve) => setTimeout(resolve, 6000));
}
if (process.env.ACTION === 'fill_shipping') {
  const testEmail = JSON.stringify(process.env.TEST_EMAIL ?? 'checkout-new-001@example.com');
  const testLastName = JSON.stringify(process.env.TEST_LAST_NAME ?? 'Guest');
  const filled = await cdp.evaluate(`(() => {
    const setValue = (selector, value) => {
      const field = document.querySelector(selector);
      if (!field) return false;
      const prototype = field instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, value);
      field.dispatchEvent(new Event('input', {bubbles: true}));
      field.dispatchEvent(new Event('change', {bubbles: true}));
      field.dispatchEvent(new Event('blur', {bubbles: true}));
      return true;
    };
    const results = [
      setValue('input[autocomplete="shipping email"]', ${testEmail}),
      setValue('select[autocomplete="shipping country-name"]', 'UA'),
      setValue('input[autocomplete="shipping given-name"]', 'Test'),
      setValue('input[autocomplete="shipping family-name"]', ${testLastName}),
      setValue('input[autocomplete="shipping street-address"]', '1 Khreshchatyk Street'),
      setValue('input[autocomplete="shipping address-level2"]', 'Kyiv'),
      setValue('input[autocomplete="shipping postal-code"]', '01001'),
    ];
    return results.every(Boolean);
  })()`);
  if (!filled) throw new Error('One or more shipping fields were not found');
  await new Promise((resolve) => setTimeout(resolve, 8000));
}
if (process.env.ACTION === 'select_money_order') {
  const selected = await cdp.evaluate(`(() => {
    const radio = [...document.querySelectorAll('input[type="radio"]')]
      .find((field) => field.labels?.[0]?.textContent?.trim() === 'Money Order');
    if (!radio) return false;
    radio.click();
    return true;
  })()`);
  if (!selected) throw new Error('Money Order payment option was not found');
  await new Promise((resolve) => setTimeout(resolve, 2500));
}
if (process.env.ACTION === 'use_shipping_as_billing') {
  const selected = await cdp.evaluate(`(() => {
    const radio = [...document.querySelectorAll('input[type="radio"]')]
      .find((field) => field.labels?.[0]?.textContent?.trim() === 'Same as shipping address');
    if (!radio) return false;
    radio.click();
    return true;
  })()`);
  if (!selected) throw new Error('Same as shipping address option was not found');
  await new Promise((resolve) => setTimeout(resolve, 2000));
}
if (process.env.ACTION === 'submit_order') {
  const submitted = await cdp.evaluate(`(() => {
    const buttons = [...document.querySelectorAll('button, input[type="submit"]')];
    const button = buttons.find((item) => !item.disabled &&
      ['pay now', 'complete order'].includes((item.textContent || item.value || '').trim().toLowerCase()));
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!submitted) throw new Error('Enabled order submission button was not found');
  await new Promise((resolve) => setTimeout(resolve, 10000));
}
const snapshot = await cdp.evaluate(`(() => ({
  url: location.href,
  title: document.title,
  text: document.body.innerText.slice(0, 4000),
  links: [...document.querySelectorAll('a[href]')]
    .map((link) => ({text: link.textContent.trim(), href: link.href}))
    .filter((item, index, all) => item.href && all.findIndex((x) => x.href === item.href) === index)
    .slice(0, 50),
  products: [...document.querySelectorAll('a[href*="/products/"]')]
    .map((link) => ({text: link.textContent.trim(), href: link.href}))
    .filter((item, index, all) => item.text && all.findIndex((x) => x.href === item.href) === index)
    .slice(0, 20),
  buttons: [...document.querySelectorAll('button, input[type="submit"]')]
    .map((button) => ({
      text: button.textContent?.trim() || button.value || button.getAttribute('aria-label'),
      disabled: button.disabled,
    }))
    .filter(Boolean)
    .slice(0, 30),
  radios: [...document.querySelectorAll('input[type="radio"]')].map((field) => ({
    label: field.labels?.[0]?.textContent?.trim(),
    name: field.name,
    checked: field.checked,
  })),
  productForms: [...document.querySelectorAll('form[action*="/cart/add"]')].map((form) => ({
    action: form.action,
    variantId: form.querySelector('[name="id"]')?.value,
    disabled: Boolean(form.querySelector('[type="submit"]:disabled')),
    submitText: form.querySelector('[type="submit"]')?.textContent?.trim(),
  })),
  fields: [...document.querySelectorAll('input, select')].map((field) => ({
    tag: field.tagName.toLowerCase(),
    type: field.type,
    name: field.name,
    id: field.id,
    autocomplete: field.autocomplete,
    placeholder: field.placeholder,
    label: field.labels?.[0]?.textContent?.trim(),
    required: field.required,
  })).filter((field) => field.type !== 'hidden').slice(0, 60),
}))()`);
if (process.env.SEARCH_QUERY) {
  const query = JSON.stringify(process.env.SEARCH_QUERY);
  snapshot.search = await cdp.evaluate(`fetch('/search/suggest.json?q=' + encodeURIComponent(${query}) + '&resources[type]=product&resources[limit]=10')
    .then(async (response) => ({status: response.status, body: await response.text()}))`);
}
if (process.env.COMPACT === '1') {
  console.log(JSON.stringify({
    url: snapshot.url,
    title: snapshot.title,
    text: snapshot.text.slice(-1200),
    buttons: snapshot.buttons,
    radios: snapshot.radios,
  }, null, 2));
} else {
  console.log(JSON.stringify(snapshot, null, 2));
}
cdp.close();
