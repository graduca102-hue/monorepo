(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  try { tg?.setHeaderColor?.('secondary_bg_color'); } catch (_) {}
  try { tg?.setBackgroundColor?.('secondary_bg_color'); } catch (_) {}

  /* ---- Font Awesome (free, solid) icon sprite ---------------------------- *
   * Icons: Font Awesome Free 6.7 — CC BY 4.0 (https://fontawesome.com).      */
  const ICONS = {
    house: ['0 0 576 512','M575.8 255.5c0 18-15 32.1-32 32.1l-32 0 .7 160.2c0 2.7-.2 5.4-.5 8.1l0 16.2c0 22.1-17.9 40-40 40l-16 0c-1.1 0-2.2 0-3.3-.1c-1.4 .1-2.8 .1-4.2 .1L416 512l-24 0c-22.1 0-40-17.9-40-40l0-24 0-64c0-17.7-14.3-32-32-32l-64 0c-17.7 0-32 14.3-32 32l0 64 0 24c0 22.1-17.9 40-40 40l-24 0-31.9 0c-1.5 0-3-.1-4.5-.2c-1.2 .1-2.4 .2-3.6 .2l-16 0c-22.1 0-40-17.9-40-40l0-112c0-.9 0-1.9 .1-2.8l0-69.7-32 0c-18 0-32-14-32-32.1c0-9 3-17 10-24L266.4 8c7-7 15-8 22-8s15 2 21 7L564.8 231.5c8 7 12 15 11 24z'],
    robot: ['0 0 640 512','M320 0c17.7 0 32 14.3 32 32l0 64 120 0c39.8 0 72 32.2 72 72l0 272c0 39.8-32.2 72-72 72l-304 0c-39.8 0-72-32.2-72-72l0-272c0-39.8 32.2-72 72-72l120 0 0-64c0-17.7 14.3-32 32-32zM208 384c-8.8 0-16 7.2-16 16s7.2 16 16 16l32 0c8.8 0 16-7.2 16-16s-7.2-16-16-16l-32 0zm96 0c-8.8 0-16 7.2-16 16s7.2 16 16 16l32 0c8.8 0 16-7.2 16-16s-7.2-16-16-16l-32 0zm96 0c-8.8 0-16 7.2-16 16s7.2 16 16 16l32 0c8.8 0 16-7.2 16-16s-7.2-16-16-16l-32 0zM264 256a40 40 0 1 0 -80 0 40 40 0 1 0 80 0zm152 40a40 40 0 1 0 0-80 40 40 0 1 0 0 80zM48 224l16 0 0 192-16 0c-26.5 0-48-21.5-48-48l0-96c0-26.5 21.5-48 48-48zm544 0c26.5 0 48 21.5 48 48l0 96c0 26.5-21.5 48-48 48l-16 0 0-192 16 0z'],
    user: ['0 0 448 512','M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3C0 498.7 13.3 512 29.7 512l388.6 0c16.4 0 29.7-13.3 29.7-29.7C448 383.8 368.2 304 269.7 304l-91.4 0z'],
    share: ['0 0 448 512','M352 224c53 0 96-43 96-96s-43-96-96-96s-96 43-96 96c0 4 .2 8 .7 11.9l-94.1 47C145.4 170.2 121.9 160 96 160c-53 0-96 43-96 96s43 96 96 96c25.9 0 49.4-10.2 66.6-26.9l94.1 47c-.5 3.9-.7 7.8-.7 11.9c0 53 43 96 96 96s96-43 96-96s-43-96-96-96c-25.9 0-49.4 10.2-66.6 26.9l-94.1-47c.5-3.9 .7-7.8 .7-11.9s-.2-8-.7-11.9l94.1-47C302.6 213.8 326.1 224 352 224z'],
    percent: ['0 0 384 512','M374.6 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-320 320c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0l320-320zM128 128A64 64 0 1 0 0 128a64 64 0 1 0 128 0zM384 384a64 64 0 1 0 -128 0 64 64 0 1 0 128 0z'],
    bullhorn: ['0 0 512 512','M480 32c0-12.9-7.8-24.6-19.8-29.6s-25.7-2.2-34.9 6.9L381.7 53c-48 48-113.1 75-181 75l-8.7 0-32 0-96 0c-35.3 0-64 28.7-64 64l0 96c0 35.3 28.7 64 64 64l0 128c0 17.7 14.3 32 32 32l64 0c17.7 0 32-14.3 32-32l0-128 8.7 0c67.9 0 133 27 181 75l43.6 43.6c9.2 9.2 22.9 11.9 34.9 6.9s19.8-16.6 19.8-29.6l0-147.6c18.6-8.8 32-32.5 32-60.4s-13.4-51.6-32-60.4L480 32zm-64 76.7L416 240l0 131.3C357.2 317.8 280.5 288 200.7 288l-8.7 0 0-96 8.7 0c79.8 0 156.5-29.8 215.3-83.3z'],
    bell: ['0 0 448 512','M224 0c-17.7 0-32 14.3-32 32l0 19.2C119 66 64 130.6 64 208l0 18.8c0 47-17.3 92.4-48.5 127.6l-7.4 8.3c-8.4 9.4-10.4 22.9-5.3 34.4S19.4 416 32 416l384 0c12.6 0 24-7.4 29.2-18.9s3.1-25-5.3-34.4l-7.4-8.3C401.3 319.2 384 273.9 384 226.8l0-18.8c0-77.4-55-142-128-156.8L256 32c0-17.7-14.3-32-32-32zm45.3 493.3c12-12 18.7-28.3 18.7-45.3l-64 0-64 0c0 17 6.7 33.3 18.7 45.3s28.3 18.7 45.3 18.7s33.3-6.7 45.3-18.7z'],
    chart: ['0 0 512 512','M32 32c17.7 0 32 14.3 32 32l0 336c0 8.8 7.2 16 16 16l400 0c17.7 0 32 14.3 32 32s-14.3 32-32 32L80 480c-44.2 0-80-35.8-80-80L0 64C0 46.3 14.3 32 32 32zM160 224c17.7 0 32 14.3 32 32l0 64c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-64c0-17.7 14.3-32 32-32zm128-64l0 160c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-160c0-17.7 14.3-32 32-32s32 14.3 32 32zm64 32c17.7 0 32 14.3 32 32l0 96c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-96c0-17.7 14.3-32 32-32zM480 96l0 224c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-224c0-17.7 14.3-32 32-32s32 14.3 32 32z'],
    money: ['0 0 576 512','M0 112.5L0 422.3c0 18 10.1 35 27 41.3c87 32.5 174 10.3 261-11.9c79.8-20.3 159.6-40.7 239.3-18.9c23 6.3 48.7-9.5 48.7-33.4l0-309.9c0-18-10.1-35-27-41.3C462 15.9 375 38.1 288 60.3C208.2 80.6 128.4 100.9 48.7 79.1C25.6 72.8 0 88.6 0 112.5zM288 352c-44.2 0-80-43-80-96s35.8-96 80-96s80 43 80 96s-35.8 96-80 96zM64 352c35.3 0 64 28.7 64 64l-64 0 0-64zm64-208c0 35.3-28.7 64-64 64l0-64 64 0zM512 304l0 64-64 0c0-35.3 28.7-64 64-64zM448 96l64 0 0 64c-35.3 0-64-28.7-64-64z'],
    wallet: ['0 0 512 512','M64 32C28.7 32 0 60.7 0 96L0 416c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-224c0-35.3-28.7-64-64-64L80 128c-8.8 0-16-7.2-16-16s7.2-16 16-16l368 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L64 32zM416 272a32 32 0 1 1 0 64 32 32 0 1 1 0-64z'],
    bolt: ['0 0 448 512','M349.4 44.6c5.9-13.7 1.5-29.7-10.6-38.5s-28.6-8-39.9 1.8l-256 224c-10 8.8-13.6 22.9-8.9 35.3S50.7 288 64 288l111.5 0L98.6 467.4c-5.9 13.7-1.5 29.7 10.6 38.5s28.6 8 39.9-1.8l256-224c10-8.8 13.6-22.9 8.9-35.3s-16.6-20.7-30-20.7l-111.5 0L349.4 44.6z'],
    catalog: ['0 0 512 512','M0 96C0 60.7 28.7 32 64 32l384 0c35.3 0 64 28.7 64 64l0 320c0 35.3-28.7 64-64 64L64 480c-35.3 0-64-28.7-64-64L0 96zm64 0l0 64 64 0 0-64L64 96zm384 0L192 96l0 64 256 0 0-64zM64 224l0 64 64 0 0-64-64 0zm384 0l-256 0 0 64 256 0 0-64zM64 352l0 64 64 0 0-64-64 0zm384 0l-256 0 0 64 256 0 0-64z'],
    gear: ['0 0 512 512','M495.9 166.6c3.2 8.7 .5 18.4-6.4 24.6l-43.3 39.4c1.1 8.3 1.7 16.8 1.7 25.4s-.6 17.1-1.7 25.4l43.3 39.4c6.9 6.2 9.6 15.9 6.4 24.6c-4.4 11.9-9.7 23.3-15.8 34.3l-4.7 8.1c-6.6 11-14 21.4-22.1 31.2c-5.9 7.2-15.7 9.6-24.5 6.8l-55.7-17.7c-13.4 10.3-28.2 18.9-44 25.4l-12.5 57.1c-2 9.1-9 16.3-18.2 17.8c-13.8 2.3-28 3.5-42.5 3.5s-28.7-1.2-42.5-3.5c-9.2-1.5-16.2-8.7-18.2-17.8l-12.5-57.1c-15.8-6.5-30.6-15.1-44-25.4L83.1 425.9c-8.8 2.8-18.6 .3-24.5-6.8c-8.1-9.8-15.5-20.2-22.1-31.2l-4.7-8.1c-6.1-11-11.4-22.4-15.8-34.3c-3.2-8.7-.5-18.4 6.4-24.6l43.3-39.4C64.6 273.1 64 264.6 64 256s.6-17.1 1.7-25.4L22.4 191.2c-6.9-6.2-9.6-15.9-6.4-24.6c4.4-11.9 9.7-23.3 15.8-34.3l4.7-8.1c6.6-11 14-21.4 22.1-31.2c5.9-7.2 15.7-9.6 24.5-6.8l55.7 17.7c13.4-10.3 28.2-18.9 44-25.4l12.5-57.1c2-9.1 9-16.3 18.2-17.8C227.3 1.2 241.5 0 256 0s28.7 1.2 42.5 3.5c9.2 1.5 16.2 8.7 18.2 17.8l12.5 57.1c15.8 6.5 30.6 15.1 44 25.4l55.7-17.7c8.8-2.8 18.6-.3 24.5 6.8c8.1 9.8 15.5 20.2 22.1 31.2l4.7 8.1c6.1 11 11.4 22.4 15.8 34.3zM256 336a80 80 0 1 0 0-160 80 80 0 1 0 0 160z'],
    search: ['0 0 512 512','M416 208c0 45.9-14.9 88.3-40 122.7L502.6 457.4c12.5 12.5 12.5 32.8 0 45.3s-32.8 12.5-45.3 0L330.7 376c-34.4 25.2-76.8 40-122.7 40C93.1 416 0 322.9 0 208S93.1 0 208 0S416 93.1 416 208zM208 352a144 144 0 1 0 0-288 144 144 0 1 0 0 288z'],
    chev: ['0 0 320 512','M310.6 233.4c12.5 12.5 12.5 32.8 0 45.3l-192 192c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L242.7 256 73.4 86.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l192 192z'],
    store: ['0 0 448 512','M160 112c0-35.3 28.7-64 64-64s64 28.7 64 64l0 48-128 0 0-48zm-48 48l-64 0c-26.5 0-48 21.5-48 48L0 416c0 53 43 96 96 96l256 0c53 0 96-43 96-96l0-208c0-26.5-21.5-48-48-48l-64 0 0-48C336 50.1 285.9 0 224 0S112 50.1 112 112l0 48zm24 48a24 24 0 1 1 0 48 24 24 0 1 1 0-48zm152 24a24 24 0 1 1 48 0 24 24 0 1 1 -48 0z'],
    plus: ['0 0 448 512','M256 80c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 144L48 224c-17.7 0-32 14.3-32 32s14.3 32 32 32l144 0 0 144c0 17.7 14.3 32 32 32s32-14.3 32-32l0-144 144 0c17.7 0 32-14.3 32-32s-14.3-32-32-32l-144 0 0-144z'],
    retry: ['0 0 512 512','M386.3 160L336 160c-17.7 0-32 14.3-32 32s14.3 32 32 32l128 0c17.7 0 32-14.3 32-32l0-128c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 51.2L414.4 97.6c-87.5-87.5-229.3-87.5-316.8 0s-87.5 229.3 0 316.8s229.3 87.5 316.8 0c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0c-62.5 62.5-163.8 62.5-226.3 0s-62.5-163.8 0-226.3s163.8-62.5 226.3 0L386.3 160z'],
    error: ['0 0 512 512','M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zm0-384c13.3 0 24 10.7 24 24l0 112c0 13.3-10.7 24-24 24s-24-10.7-24-24l0-112c0-13.3 10.7-24 24-24zM224 352a32 32 0 1 1 64 0 32 32 0 1 1 -64 0z'],
    check: ['0 0 512 512','M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM369 209L241 337c-9.4 9.4-24.6 9.4-33.9 0l-64-64c-9.4-9.4-9.4-24.6 0-33.9s24.6-9.4 33.9 0l47 47L335 175c9.4-9.4 24.6-9.4 33.9 0s9.4 24.6 0 33.9z'],
    link: ['0 0 640 512','M579.8 267.7c56.5-56.5 56.5-148 0-204.5c-50-50-128.8-56.5-186.3-15.4l-1.6 1.1c-14.4 10.3-17.7 30.3-7.4 44.6s30.3 17.7 44.6 7.4l1.6-1.1c32.1-22.9 76-19.3 103.8 8.6c31.5 31.5 31.5 82.5 0 114L422.3 334.8c-31.5 31.5-82.5 31.5-114 0c-27.9-27.9-31.5-71.8-8.6-103.8l1.1-1.6c10.3-14.4 6.9-34.4-7.4-44.6s-34.4-6.9-44.6 7.4l-1.1 1.6C206.5 251.2 213 330 263 380c56.5 56.5 148 56.5 204.5 0L579.8 267.7zM60.2 244.3c-56.5 56.5-56.5 148 0 204.5c50 50 128.8 56.5 186.3 15.4l1.6-1.1c14.4-10.3 17.7-30.3 7.4-44.6s-30.3-17.7-44.6-7.4l-1.6 1.1c-32.1 22.9-76 19.3-103.8-8.6C74 372 74 321 105.5 289.5L217.7 177.2c31.5-31.5 82.5-31.5 114 0c27.9 27.9 31.5 71.8 8.6 103.9l-1.1 1.6c-10.3 14.4-6.9 34.4 7.4 44.6s34.4 6.9 44.6-7.4l1.1-1.6C433.5 260.8 427 182 377 132c-56.5-56.5-148-56.5-204.5 0L60.2 244.3z'],
    pen: ['0 0 512 512','M471.6 21.7c-21.9-21.9-57.3-21.9-79.2 0L362.3 51.7l97.9 97.9 30.1-30.1c21.9-21.9 21.9-57.3 0-79.2L471.6 21.7zm-299.2 220c-6.1 6.1-10.8 13.6-13.5 21.9l-29.6 88.8c-2.9 8.6-.6 18.1 5.8 24.6s15.9 8.7 24.6 5.8l88.8-29.6c8.2-2.7 15.7-7.4 21.9-13.5L437.7 172.3 339.7 74.3 172.4 241.7zM96 64C43 64 0 107 0 160L0 416c0 53 43 96 96 96l256 0c53 0 96-43 96-96l0-96c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 96c0 17.7-14.3 32-32 32L96 448c-17.7 0-32-14.3-32-32l0-256c0-17.7 14.3-32 32-32l96 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L96 64z'],
    undo: ['0 0 512 512','M125.7 160l50.3 0c17.7 0 32 14.3 32 32s-14.3 32-32 32L48 224c-17.7 0-32-14.3-32-32L16 64c0-17.7 14.3-32 32-32s32 14.3 32 32l0 51.2L97.6 97.6c87.5-87.5 229.3-87.5 316.8 0s87.5 229.3 0 316.8s-229.3 87.5-316.8 0c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0c62.5 62.5 163.8 62.5 226.3 0s62.5-163.8 0-226.3s-163.8-62.5-226.3 0L125.7 160z'],
    image: ['0 0 512 512','M0 96C0 60.7 28.7 32 64 32l384 0c35.3 0 64 28.7 64 64l0 320c0 35.3-28.7 64-64 64L64 480c-35.3 0-64-28.7-64-64L0 96zM323.8 202.5c-4.5-6.6-11.9-10.5-19.8-10.5s-15.4 3.9-19.8 10.5l-87 127.6L170.7 297c-4.6-5.7-11.5-9-18.7-9s-14.2 3.3-18.7 9l-64 80c-5.8 7.2-6.9 17.1-2.9 25.4s12.4 13.6 21.6 13.6l96 0 32 0 208 0c8.9 0 17.1-4.9 21.2-12.8s3.6-17.4-1.4-24.7l-120-176zM112 192a48 48 0 1 0 0-96 48 48 0 1 0 0 96z'],
  };
  const spriteMarkup = `<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true">${Object.entries(ICONS).map(([id, [vb, d]]) => `<symbol id="ic-${id}" viewBox="${vb}"><path d="${d}"/></symbol>`).join('')}</svg>`;
  const ic = (name) => `<svg><use href="#ic-${name}"/></svg>`;

  const root = document.getElementById('app');
  const base = location.pathname.replace(/\/$/, '');
  let query = location.search;
  let data = null;
  let tab = 'overview';
  let period = 'today';
  const ROOT_TABS = ['overview', 'bots', 'profile'];

  const money = (value) => `${Number(value || 0).toFixed(2)} USDT`;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  const haptic = (kind = 'light') => { try { tg?.HapticFeedback?.impactOccurred?.(kind); } catch (_) {} };
  const notify = (kind = 'success') => { try { tg?.HapticFeedback?.notificationOccurred?.(kind); } catch (_) {} };
  const alert = (msg) => { if (tg?.showAlert) tg.showAlert(msg); else window.alert(msg); };

  const getTelegramInitData = () => {
    const sdkValue = String(window.Telegram?.WebApp?.initData || '').trim();
    if (sdkValue) return sdkValue;
    const hashValue = new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData');
    if (hashValue) return hashValue;
    const queryValue = new URLSearchParams(location.search).get('tgWebAppData');
    if (queryValue) return queryValue;
    try { return String(sessionStorage.getItem('tgWebAppData') || '').trim(); } catch (_) { return ''; }
  };
  const waitForTelegramInitData = async (timeoutMs = 1500) => {
    const startedAt = Date.now();
    let initData = getTelegramInitData();
    while (!initData && Date.now() - startedAt < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      initData = getTelegramInitData();
    }
    return initData;
  };
  const api = async (path, options = {}) => {
    const initData = await waitForTelegramInitData();
    const response = await fetch(`${base}${path}${query}`, {...options, headers: {'Content-Type':'application/json','X-Telegram-Init-Data':initData, ...(options.headers || {})}});
    const responseText = await response.text();
    let payload = {};
    try { payload = responseText ? JSON.parse(responseText) : {}; } catch (_) {}
    if (!response.ok) {
      const error = Error(payload.error || `Ошибка запроса (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };

  const productPreviewNames = ['ref-05.png', 'ref-04.png', 'ref-01.png', 'ref-02.png', 'ref-03.png'];

  /* ---- shared partials -------------------------------------------------- */
  const acctBar = () => {
    const noBot = data.has_bot === false;
    const brand = data.bot?.username ? `@${esc(data.bot.username)}` : 'Бот не подключён';
    return `<div class="acct">
      <div class="acct-logo">P</div>
      <div class="acct-meta"><b>Партнёрский кабинет</b><span>${brand}</span></div>
      <button class="acct-balance" data-tab="${noBot ? 'bots' : 'withdraw'}">
        <small>${noBot ? 'подключение' : 'к выводу'}</small>
        <b>${noBot ? 'Создать' : money(data.available_balance)}</b>
      </button>
    </div>`;
  };
  const titleBlock = (kicker, heading, sub) => `<div class="title"><h1>${esc(heading)}</h1>${sub ? `<p>${esc(sub)}</p>` : ''}</div>`;
  const cell = ({ tint, icon, title, subtitle, value, valueAccent, chevron = true, attrs = '' }) => `
    <button class="cell" ${attrs}>
      ${icon ? `<span class="cell-ico ${tint || 't-gray'}">${ic(icon)}</span>` : ''}
      <span class="cell-body"><b>${esc(title)}</b>${subtitle ? `<small>${esc(subtitle)}</small>` : ''}</span>
      ${value != null ? `<span class="cell-val${valueAccent ? ' accent' : ''}">${esc(value)}</span>` : ''}
      ${chevron ? `<span class="cell-chev">${ic('chev')}</span>` : ''}
    </button>`;
  const group = (header, rows, footer) => `
    <div class="group">
      ${header ? `<div class="group-header">${esc(header)}</div>` : ''}
      <div class="list inset">${rows}</div>
      ${footer ? `<div class="group-footer">${esc(footer)}</div>` : ''}
    </div>`;

  /* ---- screens -------------------------------------------------------- */
  const overview = () => {
    const active = data.bot.active;
    return `${acctBar()}${titleBlock('', 'Кабинет')}
      <div class="hero-balance">
        <small>Доступно к выводу</small>
        <b>${money(data.available_balance)}</b>
        <button class="btn" data-tab="withdraw" style="margin:0;width:100%">Вывести средства</button>
      </div>
      <div class="metrics">
        <div class="metric"><span>${ic('money')} Заработано</span><b>${money(data.earned)}</b></div>
        <div class="metric"><span>${ic('share')} Переходов</span><b>${data.stats.total_users || 0}</b></div>
        <div class="metric"><span>${ic('chart')} Оборот</span><b>${money(data.stats.delivered_revenue)}</b></div>
        <div class="metric"><span>${ic('wallet')} К выводу</span><b>${money(data.available_balance)}</b></div>
      </div>
      <div class="chart">
        <div class="chart-head"><span>Заработок по дням</span><button data-tab="stats">Подробнее</button></div>
        <div class="chart-empty">Пока нет данных</div>
      </div>
      ${group('Настройки бота', [
        cell({ tint:'t-pink', icon:'pen', title:'Тексты и кнопки', subtitle:'Приветствие и подписи кнопок', attrs:'data-tab="texts"' }),
        cell({ tint:'t-purple', icon:'image', title:'Картинки меню', subtitle:'Свои картинки для разделов бота', attrs:'data-tab="banners"' }),
        cell({ tint:'t-teal', icon:'share', title:'Реферальная ссылка', subtitle:'Приглашайте партнёров', attrs:'data-tab="referral"' }),
        cell({ tint:'t-blue', icon:'link', title:'UTM-ссылки', subtitle:'Отслеживайте, откуда приходят клиенты', attrs:'data-tab="utm"' }),
        cell({ tint:'t-green', icon:'percent', title:'Наценка', subtitle:'Надбавка к цене товаров', attrs:'data-tab="markup"' }),
        cell({ tint:'t-orange', icon:'bullhorn', title:'Рассылка', subtitle:'Сообщение вашим клиентам', attrs:'data-tab="broadcast"' }),
        cell({ tint:'t-indigo', icon:'bell', title:'Канал подписки', subtitle:'Доступ к боту по подписке', attrs:'data-tab="subscription"' }),
        cell({ tint:'t-teal', icon:'link', title:'Канал и поддержка', subtitle:'Ссылки в разделе «Информация»', attrs:'data-tab="contacts"' }),
        cell({ tint:'t-blue', icon:'chart', title:'Статистика', subtitle:'Переходы и начисления', attrs:'data-tab="stats"' }),
        cell({ tint:'t-green', icon:'wallet', title:'Вывод средств', subtitle:'Заявка на выплату', attrs:'data-tab="withdraw"' }),
      ].join(''))}
      ${group('Состояние', `
        <div class="cell plain">
          <span class="cell-body"><b>Подключение бота</b><small>${active ? 'Активно — бот принимает клиентов' : 'Приостановлено'}</small></span>
          <span class="switch ${active ? 'on' : ''}" data-action="toggle"><i></i></span>
        </div>`)}`;
  };

  const emptyOverview = () => `${acctBar()}
    <div class="empty">
      <div class="ico">${ic('robot')}</div>
      <h2>Бот ещё не подключён</h2>
      <p>Создайте партнёрского бота, чтобы открыть каталог, принимать клиентов и получать начисления.</p>
      <button class="btn" data-open-bots style="width:auto;min-width:220px;margin:0 auto">Подключить бота</button>
    </div>`;

  const bots = () => {
    const items = Array.isArray(data.bots) ? data.bots : [];
    const rows = items.length
      ? items.map((item) => {
          const username = String(item.username || '');
          const letter = esc(username.replace(/^@/, '').slice(0, 1).toUpperCase() || 'B');
          return `<button class="cell bot-cell" data-open-bot="${esc(username)}">
            <span class="cell-ico">${letter}</span>
            <span class="cell-body"><b>${esc(username ? `@${username}` : 'Без имени')}</b><small>${item.users || 0} польз. · ${money(item.earned || 0)}</small></span>
            <span class="bot-badge ${item.active ? 'on' : ''}">${item.active ? 'АКТИВЕН' : 'ПАУЗА'}</span>
            <span class="cell-chev">${ic('chev')}</span>
          </button>`;
        }).join('')
      : `<div class="cell plain"><span class="cell-body"><b>Пока нет подключённых ботов</b><small>Выберите готовое решение ниже</small></span></div>`;
    return `${acctBar()}${titleBlock('', 'Боты')}
      ${group('Мои боты', rows)}
      ${group('Готовые решения', [
        cell({ tint:'t-purple', icon:'store', title:'Универсальный магазин', subtitle:'Цифровые товары с автовыдачей', attrs:'data-tab="universal_store"' }),
        cell({ tint:'t-blue', icon:'plus', title:'Подключить свой бот', subtitle:'Токен от @BotFather', attrs:'data-create-bot' }),
      ].join(''))}`;
  };

  const universalStore = () => `${acctBar()}
    <div class="hero-ico">${ic('store')}</div>
    <div class="hero-txt"><h1>Универсальный магазин</h1><p>Telegram-бот для продажи цифровых товаров с автоматической выдачей после оплаты.</p></div>
    <div class="product-shot" data-product>
      ${productPreviewNames.map((name, i) => `<img src="${base}/reference/bots/${name}" alt="Экран ${i + 1}"${i ? ' hidden' : ''}>`).join('')}
      <div class="product-nav">${productPreviewNames.map((_, i) => `<i${i ? '' : ' class="active"'}></i>`).join('')}</div>
    </div>
    ${group('Что входит', [
      cell({ tint:'t-orange', icon:'bolt', title:'Автовыдача', subtitle:'Товар приходит сразу после оплаты', chevron:false }),
      cell({ tint:'t-blue', icon:'catalog', title:'Готовый каталог', subtitle:'Аккаунты, почта, VPN, прокси, SMS', chevron:false }),
      cell({ tint:'t-green', icon:'percent', title:'Своя наценка', subtitle:'Вы задаёте процент к цене', chevron:false }),
      cell({ tint:'t-indigo', icon:'gear', title:'Управление из кабинета', subtitle:'Статистика, рассылки, подписка', chevron:false }),
    ].join(''))}
    <div class="steps">
      <h3>Как подключить</h3>
      <div class="step"><b>1</b><span>Создайте нового бота через @BotFather</span></div>
      <div class="step"><b>2</b><span>Отправьте токен в SousPartnersBot</span></div>
      <div class="step"><b>3</b><span>Настройте наценку — бот готов к работе</span></div>
    </div>
    <button class="btn" data-create-bot>Создать бота</button>`;

  const createBotPage = () => `${titleBlock('', 'Подключить бота', 'Введите токен от @BotFather — после проверки бот сразу активируется.')}
    <div class="field">
      <label>Токен бота</label>
      <div class="control"><input id="create-bot-token" type="password" autocomplete="off" placeholder="123456:ABC-DEF..."></div>
      <div class="hint">Бот проверится и запустится автоматически.</div>
    </div>
    <div class="hint field-status" data-create-status aria-live="polite" style="padding:10px 20px 0;color:var(--destructive);font-size:13px"></div>`;

  const stats = () => {
    const summary = data.stats || {};
    return `${titleBlock('', 'Статистика', 'Результаты подключённого бота.')}
      <div class="segmented">${[['today','Сегодня'],['7d','7 дней'],['30d','30 дней']].map(([key, label]) => `<button class="${period === key ? 'active' : ''}" data-period="${key}">${label}</button>`).join('')}</div>
      ${group('', [
        cell({ icon:'money', tint:'t-green', title:'Начислено', value:money(summary.profit), valueAccent:true, chevron:false }),
        cell({ icon:'share', tint:'t-teal', title:'Переходов', value:String(summary.total_users || 0), chevron:false }),
        cell({ icon:'chart', tint:'t-blue', title:'Оборот', value:money(summary.delivered_revenue), chevron:false }),
        cell({ icon:'robot', tint:'t-purple', title:'Подключение', value:data.bot.active ? 'Активно' : 'Пауза', chevron:false }),
      ].join(''))}`;
  };

  const referral = () => {
    const item = data.referral || {};
    return `${titleBlock('', 'Реферальная ссылка', 'Приглашайте владельцев Telegram-ботов в партнёрскую программу.')}
      <div class="field">
        <label>Ваша ссылка</label>
        <div class="control"><input value="${esc(item.link || '')}" readonly><button class="inline-btn" data-copy="${esc(item.link || '')}">Копировать</button></div>
        <div class="hint">Приглашено участников: <b>${data.stats?.total_users || 0}</b></div>
      </div>
      ${group('Условия программы', `
        <div class="cell plain"><span class="cell-body"><b>15% от наценки</b><small>С фактически заработанной приглашённым наценки</small></span></div>
        <div class="cell plain"><span class="cell-body"><b>Закрепление</b><small>Партнёр закрепляется за вами после перехода по ссылке</small></span></div>
        <div class="cell plain"><span class="cell-body"><b>Автоначисление</b><small>Начисление после выполнения заказа</small></span></div>
        <div class="cell plain"><span class="cell-body"><b>Вывод от 5&nbsp;$</b><small>Оборот и заказы без вашей ссылки не учитываются</small></span></div>`)}`;
  };

  const markup = () => {
    const values = data.markups || {};
    const fieldFor = (kind, label) => `
      <div class="field">
        <label>${label}</label>
        <div class="control"><input class="markup-input" data-kind="${kind}" type="number" inputmode="numeric" min="0" max="500" value="${Number(values[kind] || 0)}"><button class="inline-btn" data-markup="${kind}">Сохранить</button></div>
      </div>`;
    return `${titleBlock('', 'Наценка', 'Задайте дополнительный процент к цене отдельно для каждой категории.')}
      ${fieldFor('goods', 'Цифровые товары, %')}
      ${fieldFor('proxy', 'Прокси, %')}
      ${fieldFor('sms', 'SMS, %')}`;
  };

  const broadcast = () => `${titleBlock('', 'Рассылка', 'Отправьте сообщение пользователям вашего бота.')}
    <div class="field">
      <label>Текст сообщения</label>
      <div class="control"><textarea id="broadcast-text" rows="7" maxlength="4000" placeholder="Введите сообщение"></textarea></div>
      <div class="hint">Получателей: <b>${data.stats?.total_users || 0}</b></div>
    </div>`;

  const subscription = () => {
    const item = data.subscription || {};
    return `${titleBlock('', 'Канал подписки', 'Попросите пользователя подписаться на канал перед началом работы.')}
      <div class="field">
        <label>Ссылка на канал</label>
        <div class="control"><input id="sub-url" value="${esc(item.channel_url || '')}" placeholder="https://t.me/channel"><button class="inline-btn" data-config="subscription_channel_url">Сохранить</button></div>
      </div>
      ${group('', `
        <div class="cell plain">
          <span class="cell-body"><b>Проверка подписки</b><small>${item.enabled ? 'Включена' : 'Выключена'}</small></span>
          <span class="switch ${item.enabled ? 'on' : ''}" data-config="subscription_enabled" data-value="${item.enabled ? 0 : 1}"><i></i></span>
        </div>`)}`;
  };

  const contacts = () => {
    const links = data.links || {};
    return `${titleBlock('', 'Канал и поддержка', 'Ссылки в разделе «Информация» вашего бота. По умолчанию их нет — вставьте свои. Пустое поле — кнопка не показывается.')}
      <div class="field">
        <label>Новостной канал</label>
        <div class="control"><input id="contact-news" value="${esc(links.news || '')}" placeholder="https://t.me/yourchannel"><button class="inline-btn" data-config="franchise_news_url" data-input="contact-news">Сохранить</button></div>
      </div>
      <div class="field">
        <label>Поддержка</label>
        <div class="control"><input id="contact-support" value="${esc(links.support || '')}" placeholder="https://t.me/yoursupport"><button class="inline-btn" data-config="franchise_support_url" data-input="contact-support">Сохранить</button></div>
        <div class="hint">Ссылка на ваш чат поддержки, бота или личку. Оставьте пустым, чтобы убрать кнопку.</div>
      </div>`;
  };

  const withdraw = () => {
    const minimum = Number(data.payout_minimum || 5);
    return `${titleBlock('', 'Вывод средств', 'Создайте заявку на выплату доступного баланса.')}
      <div class="hero-balance"><small>Доступно</small><b>${money(data.available_balance)}</b></div>
      ${group('Способ', `<div class="cell plain"><span class="cell-ico t-blue">${ic('wallet')}</span><span class="cell-body"><b>CryptoBot</b><small>USDT · после проверки заявки</small></span></div>`)}
      <div class="field">
        <label>Сумма, USDT</label>
        <div class="control"><input id="payout-amount" type="number" inputmode="decimal" min="${minimum}" step="0.01" max="${Number(data.available_balance || 0)}" placeholder="${minimum.toFixed(2)}"></div>
      </div>
      <div class="field">
        <label>Ваш CryptoBot username или ID</label>
        <div class="control"><input id="payout-destination" placeholder="@username"></div>
        <div class="hint">Минимальная сумма выплаты — <b>${minimum.toFixed(2)} USDT</b>.</div>
      </div>`;
  };

  const settings = () => {
    const referralItem = data.referral || {};
    const subscriptionItem = data.subscription || {};
    const franchise = data.franchise || {};
    const buttonVisible = franchise.create_button_visible !== false;
    return `${titleBlock('', 'Партнёрская программа', 'Настройте процент начисления и доступ участников.')}
      ${group('', `
        <div class="cell plain">
          <span class="cell-body"><b>Кнопка приглашения в боте</b><small>Показывает переход в SousPartners для своих партнёрских ботов</small></span>
          <span class="switch ${buttonVisible ? 'on' : ''}" data-config="franchise_hide_create" data-value="${buttonVisible ? 1 : 0}"><i></i></span>
        </div>`)}
      <div class="field">
        <label>Процент партнёра</label>
        <div class="control"><input id="ref-percent" type="number" inputmode="numeric" min="0" max="100" value="${Number(referralItem.percent || 0)}"><button class="inline-btn" data-config="referral_percent">Сохранить</button></div>
      </div>
      <div class="field">
        <label>Канал подписки</label>
        <div class="control"><input id="sub-url" value="${esc(subscriptionItem.channel_url || '')}" placeholder="https://t.me/channel"><button class="inline-btn" data-config="subscription_channel_url">Сохранить</button></div>
      </div>`;
  };

  /* ---- Тексты и кнопки ---------------------------------------------- */
  let textsState = null;      // {items:[...]} once loaded
  let editingKey = null;      // key of the item being edited, or null = list

  const loadTexts = async () => {
    textsState = null;
    editingKey = null;
    render();
    try {
      textsState = await api('/api/texts');
    } catch (error) {
      textsState = { error: error.message || 'Не удалось загрузить' };
    }
    render();
  };

  const textsList = () => {
    if (!textsState) return `${titleBlock('', 'Тексты и кнопки')}<div class="loading">Загрузка…</div>`;
    if (textsState.error) return `${titleBlock('', 'Тексты и кнопки')}<div class="notice">${esc(textsState.error)}</div>`;
    const items = Array.isArray(textsState.items) ? textsState.items : [];
    const groups = [];
    for (const it of items) {
      let g = groups.find((x) => x.name === it.group);
      if (!g) { g = { name: it.group, rows: [] }; groups.push(g); }
      g.rows.push(it);
    }
    const body = groups.map((g) => group(g.name, g.rows.map((it) => {
      const preview = it.value || it.default || '—';
      return `<button class="cell" data-edit-text="${esc(it.key)}">
        <span class="cell-body"><b>${esc(it.label)}</b><small>${esc(preview)}</small></span>
        ${it.changed ? '<span class="bot-badge on">ИЗМ.</span>' : ''}
        <span class="cell-chev">${ic('chev')}</span>
      </button>`;
    }).join(''))).join('');
    return `${titleBlock('', 'Тексты и кнопки', 'Замените тексты и подписи кнопок, которые ваш бот показывает клиентам.')}${body}`;
  };

  const textsEditor = () => {
    const it = (textsState?.items || []).find((x) => x.key === editingKey);
    if (!it) { editingKey = null; return textsList(); }
    const current = it.value || '';
    const control = it.multiline
      ? `<textarea id="text-value" rows="6" maxlength="${it.max_length}" placeholder="${esc(it.default || 'Текст')}">${esc(current)}</textarea>`
      : `<input id="text-value" maxlength="${it.max_length}" placeholder="${esc(it.default || '')}" value="${esc(current)}">`;
    return `${titleBlock('', it.label, it.hint || '')}
      <div class="field">
        <label>Ваш вариант</label>
        <div class="control">${control}</div>
        <div class="hint">Стандартно: <b>${esc(it.default || '—')}</b></div>
      </div>
      ${it.changed ? `<button class="btn danger" data-reset-text="${esc(it.key)}">${ic('undo')}&nbsp; Вернуть стандартный</button>` : ''}`;
  };

  const texts = () => (editingKey ? textsEditor() : textsList());

  /* ---- UTM-ссылки ---------------------------------------------- */
  let utmState = null;        // {items:[...], top_sources:[...]} once loaded

  const loadUtm = async () => {
    utmState = null;
    render();
    try {
      utmState = await api('/api/utm');
    } catch (error) {
      utmState = { error: error.message || 'Не удалось загрузить' };
    }
    render();
  };

  const utm = () => {
    if (!utmState) return `${titleBlock('', 'UTM-ссылки')}<div class="loading">Загрузка…</div>`;
    if (utmState.error) return `${titleBlock('', 'UTM-ссылки')}<div class="notice">${esc(utmState.error)}</div>`;
    const items = Array.isArray(utmState.items) ? utmState.items : [];
    const rows = items.length
      ? items.map((it) => `<div class="cell plain">
          <span class="cell-body"><b>${esc(it.source)}</b><small>${it.clicks || 0} переходов · ${money(it.revenue)}</small></span>
          <button class="inline-btn" data-copy="${esc(it.url)}">Копировать</button>
          <button class="inline-btn ghost" data-del-utm="${esc(it.source)}">Удалить</button>
        </div>`).join('')
      : `<div class="cell plain"><span class="cell-body"><b>Ссылок пока нет</b><small>Создайте первую — например instagram</small></span></div>`;
    const top = Array.isArray(utmState.top_sources) ? utmState.top_sources : [];
    const topBlock = top.length
      ? group('Откуда приходят', top.map((row) => `<div class="cell plain">
          <span class="cell-body"><b>${esc(row.source)}</b><small>${row.clicks || 0} переходов</small></span>
          <span class="cell-val accent">${esc(money(row.revenue))}</span>
        </div>`).join(''))
      : '';
    return `${titleBlock('', 'UTM-ссылки', 'Своя ссылка для каждой площадки — видно, откуда пришли клиенты и сколько они купили.')}
      <div class="field">
        <label>Название источника</label>
        <div class="control"><input id="utm-source" maxlength="40" placeholder="instagram"><button class="inline-btn" data-add-utm>Создать</button></div>
        <div class="hint">Латиница, цифры и дефис. Ссылка: <b>https://t.me/${esc(utmState.bot_username || 'bot')}?start=utm_<i>источник</i></b></div>
      </div>
      ${group('Мои ссылки', rows)}
      ${topBlock}`;
  };

  async function submitUtm(source, remove = false) {
    const value = String(source || '').trim();
    if (!value) { alert('Введите название источника'); return; }
    try {
      const result = await api('/api/utm', { method:'POST', body:JSON.stringify({ source: value, delete: remove }) });
      utmState = result;
      notify('success');
      render();
    } catch (error) { notify('error'); alert(error.message); }
  }

  /* ---- Картинки меню ---------------------------------------------- */
  let bannersState = null;     // {items:[...]} once loaded
  let bannerKey = null;        // key of the screen being edited, or null = list
  let bannerBust = 0;          // cache-buster bumped after every change

  const loadBanners = async () => {
    bannersState = null;
    bannerKey = null;
    render();
    try {
      bannersState = await api('/api/banners');
    } catch (error) {
      bannersState = { error: error.message || 'Не удалось загрузить' };
    }
    render();
  };

  const bannerPreviewUrl = (key) => `${base}/api/banner-preview${query ? query + '&' : '?'}key=${encodeURIComponent(key)}&v=${bannerBust}`;

  const bannersList = () => {
    if (!bannersState) return `${titleBlock('', 'Картинки меню')}<div class="loading">Загрузка…</div>`;
    if (bannersState.error) return `${titleBlock('', 'Картинки меню')}<div class="notice">${esc(bannersState.error)}</div>`;
    const items = Array.isArray(bannersState.items) ? bannersState.items : [];
    const rows = items.map((it) => `<button class="cell" data-edit-banner="${esc(it.key)}">
        <span class="banner-thumb"><img src="${bannerPreviewUrl(it.key)}" alt="" loading="lazy"></span>
        <span class="cell-body"><b>${esc(it.label)}</b><small>${esc(it.hint || '')}</small></span>
        ${it.changed ? '<span class="bot-badge on">СВОЯ</span>' : ''}
        <span class="cell-chev">${ic('chev')}</span>
      </button>`).join('');
    return `${titleBlock('', 'Картинки меню', 'Загрузите свои картинки для разделов бота. Пусто — используется стандартная картинка SOUS.')}${group('Разделы', rows)}`;
  };

  const bannersEditor = () => {
    const it = (bannersState?.items || []).find((x) => x.key === bannerKey);
    if (!it) { bannerKey = null; return bannersList(); }
    return `${titleBlock('', it.label, it.hint || '')}
      <div class="banner-preview"><img src="${bannerPreviewUrl(it.key)}" alt=""></div>
      <label class="btn" for="banner-file">${ic('image')}&nbsp; Выбрать картинку</label>
      <input id="banner-file" type="file" accept="image/jpeg,image/png,image/webp" hidden>
      <div class="field"><div class="hint">JPG, PNG или WebP. Рекомендуется 1280×720. Большие файлы уменьшаются автоматически.</div></div>
      ${it.changed ? `<button class="btn danger" data-reset-banner="${esc(it.key)}">${ic('undo')}&nbsp; Вернуть стандартную</button>` : ''}`;
  };

  const banners = () => (bannerKey ? bannersEditor() : bannersList());

  // Downscale + re-encode client-side so uploads stay small and EXIF is stripped.
  const prepareImage = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(Error('Не удалось прочитать файл'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(Error('Это не изображение'));
      img.onload = () => {
        const maxW = 1280;
        const scale = Math.min(1, maxW / img.width);
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
        resolve(dataUrl.split(',', 2)[1]);
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });

  async function submitBanner(file) {
    const it = (bannersState?.items || []).find((x) => x.key === bannerKey);
    if (!it || !file) return;
    let payload;
    try { payload = await prepareImage(file); }
    catch (error) { notify('error'); alert(error.message || 'Не удалось обработать картинку'); return; }
    try {
      await api('/api/banner-file', { method:'POST', body:JSON.stringify({ key: it.key, data: payload }) });
      it.changed = true;
      bannerBust = Date.now();
      notify('success');
      bannerKey = null;
      render();
    } catch (error) { notify('error'); alert(error.message); }
  }

  async function resetBanner(key) {
    const it = (bannersState?.items || []).find((x) => x.key === key);
    if (!it) return;
    try {
      await api('/api/banners', { method:'POST', body:JSON.stringify({ key, reset: true }) });
      it.changed = false;
      bannerBust = Date.now();
      notify('success');
      bannerKey = null;
      render();
    } catch (error) { notify('error'); alert(error.message); }
  }

  const profile = () => `${acctBar()}${titleBlock('', 'Профиль')}
    ${group('', [
      cell({ icon:'robot', tint:'t-purple', title:'Подключённый бот', value:`@${esc(data.bot.username)}`, chevron:false }),
      cell({ icon:'bolt', tint:'t-orange', title:'Статус', value:data.bot.active ? 'Активен' : 'Приостановлен', chevron:false }),
      cell({ icon:'user', tint:'t-gray', title:'ID владельца', value:String(data.bot.owner_id ?? '—'), chevron:false }),
    ].join(''))}
    ${group('', [
      cell({ icon:'percent', tint:'t-green', title:'Настройки программы', attrs:'data-tab="settings"' }),
    ].join(''))}`;

  const nav = () => `<nav class="tabbar">${[
    ['overview', 'house', 'Главная'],
    ['bots', 'robot', 'Боты'],
    ['profile', 'user', 'Профиль'],
  ].map(([key, icon, label]) => `<button class="${tab === key ? 'active' : ''}" data-tab="${key}">${ic(icon)}<span>${label}</span></button>`).join('')}</nav>`;

  const SCREENS = {
    overview: () => (data.has_bot === false ? emptyOverview() : overview()),
    bots, create_bot: createBotPage, universal_store: universalStore,
    stats, withdraw, referral, markup, broadcast, subscription, contacts, settings, texts, banners, utm, profile,
  };

  /* ---- MainButton / BackButton per screen ----------------------------- */
  const MAIN_BUTTON = {
    create_bot: { text: 'Подключить бота', run: submitCreateBot },
    withdraw: { text: 'Создать заявку', run: submitPayout },
    broadcast: { text: 'Отправить сообщение', run: submitBroadcast },
  };
  let mainButtonHandler = null;
  const syncTelegramButtons = () => {
    if (!tg) return;
    try {
      tg.BackButton?.[ROOT_TABS.includes(tab) ? 'hide' : 'show']?.();
    } catch (_) {}
    const mb = tg.MainButton;
    if (!mb) return;
    if (mainButtonHandler) { mb.offClick(mainButtonHandler); mainButtonHandler = null; }
    const spec = (tab === 'texts' && editingKey) ? { text: 'Сохранить', run: submitText } : MAIN_BUTTON[tab];
    if (spec) {
      mainButtonHandler = () => spec.run();
      mb.setText(spec.text);
      mb.onClick(mainButtonHandler);
      mb.show();
      mb.enable();
    } else {
      mb.hide();
    }
  };

  function goTab(next) {
    tab = next;
    if (ROOT_TABS.includes(next)) period = 'today';
    if (next === 'texts') { loadTexts(); return; }
    if (next === 'banners') { loadBanners(); return; }
    if (next === 'utm') { loadUtm(); return; }
    render();
  }
  function goBack() {
    if (tab === 'texts' && editingKey) { editingKey = null; render(); return; }
    if (tab === 'banners' && bannerKey) { bannerKey = null; render(); return; }
    tab = data?.has_bot === false ? 'overview' : (tab === 'universal_store' || tab === 'create_bot' ? 'bots' : 'overview');
    render();
  }

  /* ---- actions ------------------------------------------------------- */
  async function submitCreateBot() {
    const input = root.querySelector('#create-bot-token');
    const status = root.querySelector('[data-create-status]');
    const token = String(input?.value || '').trim();
    if (!token) { if (status) status.textContent = 'Введите токен бота.'; return; }
    tg?.MainButton?.showProgress?.();
    if (status) status.textContent = '';
    try {
      const result = await api('/api/create-bot', { method:'POST', body:JSON.stringify({ token }) });
      const username = String(result.bot?.username || '').trim();
      if (username) { const nextUrl = new URL(location.href); nextUrl.searchParams.set('cabinet_bot', username); history.replaceState({}, '', nextUrl.toString()); query = nextUrl.search; }
      data = await api('/api/dashboard');
      notify('success');
      tab = 'overview';
      render();
    } catch (error) {
      notify('error');
      if (status) status.textContent = error.message || 'Не удалось подключить бота.';
    } finally {
      tg?.MainButton?.hideProgress?.();
    }
  }
  async function submitPayout() {
    const amount = Number(root.querySelector('#payout-amount')?.value);
    const destination = root.querySelector('#payout-destination')?.value;
    tg?.MainButton?.showProgress?.();
    try {
      const result = await api('/api/payout', { method:'POST', body:JSON.stringify({ amount, destination }) });
      notify('success');
      alert(`Заявка №${result.request_id} создана`);
      tab = 'overview';
      render();
    } catch (error) { notify('error'); alert(error.message); }
    finally { tg?.MainButton?.hideProgress?.(); }
  }
  async function submitBroadcast() {
    const field = root.querySelector('#broadcast-text');
    const text = field?.value || '';
    tg?.MainButton?.showProgress?.();
    try {
      const result = await api('/api/broadcast', { method:'POST', body:JSON.stringify({ text }) });
      notify('success');
      alert(`Отправлено: ${result.sent}`);
      if (field) field.value = '';
    } catch (error) { notify('error'); alert(error.message); }
    finally { tg?.MainButton?.hideProgress?.(); }
  }
  async function submitText() {
    const it = (textsState?.items || []).find((x) => x.key === editingKey);
    if (!it) return;
    const value = String(root.querySelector('#text-value')?.value || '').trim();
    tg?.MainButton?.showProgress?.();
    try {
      const result = await api('/api/texts', { method:'POST', body:JSON.stringify({ key: it.key, value }) });
      it.value = result.value || '';
      it.changed = !!result.changed;
      notify('success');
      editingKey = null;
      render();
    } catch (error) { notify('error'); alert(error.message); }
    finally { tg?.MainButton?.hideProgress?.(); }
  }
  async function resetText(key) {
    const it = (textsState?.items || []).find((x) => x.key === key);
    if (!it) return;
    tg?.MainButton?.showProgress?.();
    try {
      await api('/api/texts', { method:'POST', body:JSON.stringify({ key, reset: true }) });
      it.value = '';
      it.changed = false;
      notify('success');
      editingKey = null;
      render();
    } catch (error) { notify('error'); alert(error.message); }
    finally { tg?.MainButton?.hideProgress?.(); }
  }

  function bind() {
    root.querySelectorAll('[data-product]').forEach((carousel) => {
      const cards = Array.from(carousel.querySelectorAll('img'));
      const dots = Array.from(carousel.querySelectorAll('.product-nav i'));
      let index = 0;
      const show = (next) => {
        index = (next + cards.length) % cards.length;
        cards.forEach((card, i) => { card.hidden = i !== index; });
        dots.forEach((dot, i) => dot.classList.toggle('active', i === index));
      };
      let startX = 0;
      carousel.addEventListener('touchstart', (event) => { startX = event.touches[0].clientX; }, { passive: true });
      carousel.addEventListener('touchend', (event) => {
        const delta = event.changedTouches[0].clientX - startX;
        if (Math.abs(delta) > 40) { show(index + (delta < 0 ? 1 : -1)); haptic(); }
      });
      dots.forEach((dot, i) => dot.addEventListener('click', () => { show(i); haptic(); }));
    });
    root.querySelectorAll('[data-tab]').forEach((element) => element.onclick = () => { haptic(); goTab(element.dataset.tab); });
    root.querySelectorAll('[data-period]').forEach((element) => element.onclick = () => { haptic(); period = element.dataset.period; render(); });
    root.querySelectorAll('[data-edit-text]').forEach((element) => element.onclick = () => { haptic(); editingKey = element.dataset.editText; render(); });
    root.querySelectorAll('[data-reset-text]').forEach((element) => element.onclick = () => { haptic(); resetText(element.dataset.resetText); });
    root.querySelectorAll('[data-add-utm]').forEach((element) => element.onclick = () => { haptic(); submitUtm(root.querySelector('#utm-source')?.value); });
    root.querySelectorAll('[data-del-utm]').forEach((element) => element.onclick = () => { haptic(); submitUtm(element.dataset.delUtm, true); });
    root.querySelectorAll('[data-edit-banner]').forEach((element) => element.onclick = () => { haptic(); bannerKey = element.dataset.editBanner; render(); });
    root.querySelectorAll('[data-reset-banner]').forEach((element) => element.onclick = () => { haptic(); resetBanner(element.dataset.resetBanner); });
    const bannerFile = root.querySelector('#banner-file');
    if (bannerFile) bannerFile.onchange = () => { const f = bannerFile.files && bannerFile.files[0]; if (f) submitBanner(f); };
    root.querySelectorAll('[data-open-bot]').forEach((element) => element.onclick = async () => {
      const username = String(element.dataset.openBot || '').trim();
      if (!username) return;
      haptic();
      const nextUrl = new URL(location.href);
      nextUrl.searchParams.set('cabinet_bot', username);
      history.replaceState({}, '', nextUrl.toString());
      query = nextUrl.search;
      try { data = await api('/api/dashboard'); tab = 'overview'; render(); }
      catch (error) { alert(error.message); }
    });
    root.querySelectorAll('[data-open-bots]').forEach((element) => element.onclick = () => { haptic(); goTab('bots'); });
    root.querySelectorAll('[data-create-bot]').forEach((element) => element.onclick = () => { haptic(); tab = 'create_bot'; render(); });
    root.querySelectorAll('[data-copy]').forEach((element) => element.onclick = async () => {
      try { await navigator.clipboard?.writeText(element.dataset.copy || ''); } catch (_) {}
      notify('success');
      alert('Ссылка скопирована');
    });
    root.querySelectorAll('[data-action="toggle"]').forEach((element) => element.onclick = async () => {
      haptic();
      try { const result = await api('/api/action', { method:'POST', body:JSON.stringify({ action:'toggle' }) }); data.bot.active = result.active; render(); }
      catch (error) { alert(error.message); }
    });
    root.querySelectorAll('[data-markup]').forEach((element) => element.onclick = async () => {
      const kind = element.dataset.markup;
      const input = root.querySelector(`[data-kind="${kind}"]`);
      try {
        const result = await api('/api/settings', { method:'POST', body:JSON.stringify({ kind, value:Number(input.value) }) });
        data.markups = data.markups || {};
        data.markups[kind] = result.value;
        notify('success');
        alert('Наценка сохранена');
      } catch (error) { notify('error'); alert(error.message); }
    });
    root.querySelectorAll('[data-config]').forEach((element) => element.onclick = async () => {
      const field = element.dataset.config;
      const inputId = element.dataset.input || (field === 'referral_percent' ? 'ref-percent' : 'sub-url');
      const value = element.dataset.value !== undefined
        ? element.dataset.value
        : document.getElementById(inputId).value.trim();
      try {
        await api('/api/config', { method:'POST', body:JSON.stringify({ field, value }) });
        if (field === 'franchise_hide_create') { data.franchise = data.franchise || {}; data.franchise.create_button_visible = Number(value) !== 1; }
        if (field === 'subscription_enabled') { data.subscription = data.subscription || {}; data.subscription.enabled = Number(value) === 1; }
        if (field === 'subscription_channel_url') { data.subscription = data.subscription || {}; data.subscription.channel_url = value; }
        if (field === 'franchise_news_url') { data.links = data.links || {}; data.links.news = value; }
        if (field === 'franchise_support_url') { data.links = data.links || {}; data.links.support = value; }
        notify('success');
        render();
      } catch (error) { notify('error'); alert(error.message); }
    });
  }

  function render() {
    const screen = (SCREENS[tab] || SCREENS.overview)();
    root.innerHTML = `${spriteMarkup}<div class="shell">${screen}</div>${nav()}`;
    bind();
    syncTelegramButtons();
    window.scrollTo(0, 0);
  }

  tg?.BackButton?.onClick?.(() => { haptic(); goBack(); });

  if (location.search.includes('__preview')) window.__cab = { setTab: goTab, back: goBack };

  const loadDashboard = async () => {
    try {
      data = await api('/api/dashboard');
      render();
    } catch (error) {
      const telegramUrl = 'https://t.me/SousPartnersBot?start=cabinet';
      const hasTelegramData = Boolean(getTelegramInitData());
      tg?.MainButton?.hide?.();
      tg?.BackButton?.hide?.();
      if (!hasTelegramData || error.status === 401) {
        root.innerHTML = `${spriteMarkup}<div class="screen"><div class="ico">${ic('retry')}</div><h1>Перезапустите кабинет</h1><p>Telegram не передал данные сессии. Вернитесь в SousPartnersBot и снова нажмите «Кабинет».</p><button class="btn" data-retry-dashboard>Повторить</button><a class="btn ghost" href="${telegramUrl}">Открыть SousPartnersBot</a></div>`;
      } else {
        root.innerHTML = `${spriteMarkup}<div class="screen"><div class="ico">${ic('error')}</div><h1>Не удалось загрузить кабинет</h1><p>${esc(error.message || 'Временная ошибка соединения.')}</p><button class="btn" data-retry-dashboard>Повторить</button></div>`;
      }
      root.querySelector('[data-retry-dashboard]')?.addEventListener('click', loadDashboard);
    }
  };
  loadDashboard();
})();
