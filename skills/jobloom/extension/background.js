// Opens the side panel when the user clicks the toolbar button. That click is the only
// thing that starts a reading; the worker holds no timers and no listeners on navigation.
chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
