/** Agent Hooks — safe to add to your existing project. */
function postToMake(payload){
  var url = PropertiesService.getScriptProperties().getProperty('MAKE_WEBHOOK_URL');
  if (!url){ Logger.log('Missing MAKE_WEBHOOK_URL'); return false; }
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  return res.getResponseCode() >= 200 && res.getResponseCode() < 300;
}
