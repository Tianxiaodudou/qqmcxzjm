/* ==========================================================
   目录授权流程（trim.file.userAccess）
   - 宿主内直接调用 pickUserFile（sdk.isStandaloneWeb === false）
   - 独立浏览器中由用户点击触发 openAppAuth，回调页校验 state 后同源回传
   ========================================================== */

const APP_NAME = 'qqmusic-downloader';
const CALLBACK_PATH = `/app/${APP_NAME}/callback.html`;
const STATE_KEY = `${APP_NAME}:auth-state`;
const RESULT_TYPE = `${APP_NAME}:auth-result`;
const SIDEBAR_GROUP = ['myFiles', 'otherShare', 'favorites'];

export function createAuthState() {
  const state = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    sessionStorage.setItem(STATE_KEY, state);
  } catch (e) {
    /* 隐私模式下忽略 */
  }
  return state;
}

export function consumeAndValidateState(returnedState) {
  let expected = '';
  try {
    expected = sessionStorage.getItem(STATE_KEY) || '';
    sessionStorage.removeItem(STATE_KEY);
  } catch (e) {
    expected = '';
  }
  return Boolean(expected && returnedState && expected === returnedState);
}

/* 仅用于独立浏览器（isStandaloneWeb === true）：弹出授权窗口，结果由回调页回传 */
export function listenForAuthResult(onResult) {
  const listener = (event) => {
    /* 同源校验：拒绝任何非同源窗口的消息 */
    if (event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.type !== RESULT_TYPE) return;
    try {
      onResult(data.result);
    } catch (e) {
      /* 忽略业务处理异常，避免影响监听器 */
    }
  };
  window.addEventListener('message', listener);
  return () => window.removeEventListener('message', listener);
}

export async function requestUserDirectory(sdk) {
  if (!sdk) throw new Error('当前不在飞牛 fnOS 应用内，无法调用目录授权');

  if (sdk.isStandaloneWeb === true) {
    const state = createAuthState();
    await sdk.openAppAuth(
      'pickUserFile',
      {
        appName: APP_NAME,
        directory: true,
        sidebarGroup: SIDEBAR_GROUP,
        redirectUri: CALLBACK_PATH,
        state,
      },
      { target: '_blank', features: 'width=750,height=630' },
    );
    /* 结果由回调页 postMessage 回传，此处不返回路径 */
    return [];
  }

  const result = await sdk.pickUserFile({
    directory: true,
    title: '选择下载目录',
    okText: '确认授权',
    sidebarGroup: SIDEBAR_GROUP,
  });
  if (!result || result.code !== 0) {
    throw new Error((result && result.msg) || '目录授权未完成');
  }
  return result.data || [];
}

/* 仅在同源回调页执行 */
export async function handleAuthCallback() {
  const status = document.getElementById('auth-status');
  let result = null;
  try {
    const mod = await import('./trim-web-app.js');
    const App = mod.TrimApp || mod.default;
    const sdk = new App();
    if (typeof sdk.parseAppAuthCallback === 'function') {
      result = sdk.parseAppAuthCallback(window.location.href);
    }
  } catch (e) {
    result = null;
  }

  const returnedState = result && typeof result.state === 'string' ? result.state : undefined;
  if (!consumeAndValidateState(returnedState)) {
    if (status) status.textContent = '授权回调校验失败，请返回应用重试。';
    return false;
  }

  if (window.opener && !window.opener.closed) {
    window.opener.postMessage({ type: RESULT_TYPE, result }, window.location.origin);
  }
  window.close();
  return true;
}
