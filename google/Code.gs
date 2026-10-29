/* OTTv2 Google bridge. No passwords or secret keys in this source file.
 * Deployment: execute as owner; network endpoint authenticates every POST.
 * Keep the Form and registration spreadsheet private. Do not link a raw
 * response sheet: this bridge stores only password-free metadata in DangKy.
 */
const OTT = Object.freeze({
  form: '1siBRg_dxdgz6U1xMG3VLJoIozofTQIo90lrVEK1P_zc',
  resetForm: '1GbtBWsEHlIWkbHGi67FLwsk50NrI-Dhi8reviTlFxRQ',
  registration: '1n4n_UdcMDUHTo6ueh7CJtdu2ZGQe-jNQHJwEBAt9OwY',
  ranking: '1ovIv69XS_Z1LllldzG0L0Axfgg4oRLJ722S1_lTJJyA',
  registrationTab: 'DangKy', rankingTab: 'XepHang',
  resetTab: 'MatKhau',
  legacyResetQuestions: ['Mã sinh viên', 'Mật khẩu mới cho OTTv2 (12–256 ký tự; không dùng mật khẩu Google)'],
  resetQuestions: ['Mã sinh viên', 'Nội dung cần thay đổi',
    'Mật khẩu mới cho OTTv2 (12–256 ký tự; không dùng mật khẩu Google)', 'Link GitHub mới'],
  resetChoices: ['Đổi / quên mật khẩu', 'Đổi link GitHub', 'Đổi cả mật khẩu và link GitHub'],
  resetHeader: ['Mã phản hồi', 'Thời điểm', 'Email xác minh', 'Mã sinh viên', 'Trạng thái xử lý', 'Thông báo'],
  questions: ['Mã sinh viên', 'Họ tên', 'Link GitHub',
    'Mật khẩu riêng cho OTTv2 (12–256 ký tự; không dùng mật khẩu Google)'],
  header: ['Mã phản hồi', 'Thời điểm', 'Email xác minh', 'Mã sinh viên', 'Họ tên',
    'Link GitHub', 'Trạng thái xử lý', 'Thông báo'],
  rankHeader: ['Hạng', 'Mã thi đấu', 'Tên hiển thị', 'Điểm Elo', 'Số trận',
    'Thắng', 'Hòa', 'Thua', 'Cập nhật lúc']
});

function doGet() {
  return json_({ok: true, service: 'OTTv2', bridge_version: 3,
    message: 'POST có chữ ký mới được truy cập dữ liệu.'});
}

function doPost(e) {
  let lock;
  try {
    const properties = PropertiesService.getScriptProperties();
    const key = properties.getProperty('OTTV2_SECRET');
    if (!key || !/^[a-f0-9]{64}$/.test(key)) throw Error('setup');
    const text = e && e.postData && e.postData.contents;
    if (typeof text !== 'string' || text.length > 1500000) throw Error('size');
    const request = JSON.parse(text);
    if (!Number.isSafeInteger(request.timestamp) || Math.abs(Date.now()/1000-request.timestamp) > 300
        || typeof request.nonce !== 'string' || !/^[a-f0-9]{32}$/.test(request.nonce)
        || typeof request.signature !== 'string' || !/^[a-f0-9]{64}$/.test(request.signature)
        || typeof request.data !== 'string') throw Error('request');
    const message = request.timestamp+'\n'+request.nonce+'\n'+request.data;
    const actual = Utilities.computeHmacSha256Signature(message, key, Utilities.Charset.UTF_8)
      .map(b => ((b+256)%256).toString(16).padStart(2,'0')).join('');
    let mismatch = 0;
    for (let i=0; i<64; ++i) mismatch |= actual.charCodeAt(i)^request.signature.charCodeAt(i);
    if (mismatch) throw Error('auth');
    lock = LockService.getScriptLock();
    lock.waitLock(10000);
    const cache = CacheService.getScriptCache();
    if (cache.get(request.nonce)) throw Error('replay');
    cache.put(request.nonce, '1', 600);
    const data = JSON.parse(request.data);
    let answer;
    if (data.action === 'health') answer = checkSetup();
    else if (data.action === 'pull') answer = pull_();
    else if (data.action === 'ack') answer = ack_(data.receipts);
    else if (data.action === 'pull_reset') answer = pullReset_();
    else if (data.action === 'ack_reset') answer = ack_(data.receipts, true);
    else if (data.action === 'ranking') answer = ranking_(data);
    else throw Error('action');
    return json_({ok:true, ...answer});
  } catch (error) {
    // Google execution logs and HTTP errors must never contain form values.
    return json_({ok:false, error:'Không xử lý được. Kiểm tra cấu hình và quyền truy cập.'});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}

function json_(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}

function form_(reset = false) {
  const form = FormApp.openById(reset ? OTT.resetForm : OTT.form);
  // Verified mode is reviewed in Forms UI, then explicitly attested by owner.
  // FormApp exposes collectsEmail, but does not distinguish responder-input
  // from verified. Recheck that UI if the owner changes email collection.
  if (PropertiesService.getScriptProperties().getProperty(reset ? 'OTTV2_RESET_VERIFIED_EMAIL' : 'OTTV2_VERIFIED_EMAIL') !== 'reviewed-verified'
      || !form.collectsEmail()) throw Error('email');
  let destination;
  try {
    destination = form.getDestinationId();
  } catch (error) {
    // Google localizes the no-destination exception by request language.
    // Only these exact no-destination messages are safe to treat as unlinked;
    // permission, network and other failures must still stop synchronization.
    const noDestination = ['Biểu mẫu hiện không có đích trả lời.',
      'The form currently has no response destination.'];
    if (!noDestination.includes(error.message)) throw error;
  }
  if (destination) throw Error('raw response sheet');
  const items = form.getItems();
  const legacy = reset && items.length === OTT.legacyResetQuestions.length;
  const questions = reset ? (legacy ? OTT.legacyResetQuestions : OTT.resetQuestions) : OTT.questions;
  if (items.length !== questions.length || items.some((q,i) => {
    if (q.getTitle() !== questions[i]) return true;
    if (reset && !legacy && i === 1) {
      if (q.getType() !== FormApp.ItemType.MULTIPLE_CHOICE) return true;
      const choice = q.asMultipleChoiceItem();
      return !choice.isRequired() || choice.getChoices().map(c=>c.getValue()).join('\n') !== OTT.resetChoices.join('\n');
    }
    if (q.getType() !== FormApp.ItemType.TEXT) return true;
    const required = !reset || legacy || i === 0;
    return q.asTextItem().isRequired() !== required;
  })) throw Error('schema');
  return form;
}

function sheet_(id, name, header) {
  const sheet = SpreadsheetApp.openById(id).getSheetByName(name);
  if (!sheet || sheet.getRange(1,1,1,header.length).getValues()[0].join('\n') !== header.join('\n'))
    throw Error('sheet schema');
  return sheet;
}

function checkSetup() {
  const form = form_();
  const reset = form_(true);
  sheet_(OTT.registration, OTT.registrationTab, OTT.header);
  sheet_(OTT.registration, OTT.resetTab, OTT.resetHeader);
  sheet_(OTT.ranking, OTT.rankingTab, OTT.rankHeader);
  // This function intentionally reveals no secret or student data in logs.
  return {service:'OTTv2', bridge_version:3, form_id:form.getId(), reset_form_id:reset.getId(),
    account_change_form:reset.getItems().length === OTT.resetQuestions.length, ready:true};
}

function safeCell_(value) {
  const text = String(value == null ? '' : value);
  return /^[\s]*[=+\-@]/.test(text) ? "'"+text : text;
}

function queueRows_(sheet, width = 8) {
  const last = sheet.getLastRow();
  if (last > 20000) throw Error('queue full');
  return last < 2 ? [] : sheet.getRange(2,1,last-1,width).getValues();
}

function pull_() {
  const form = form_();
  const sheet = sheet_(OTT.registration, OTT.registrationTab, OTT.header);
  const known = new Map(queueRows_(sheet).map((row,i) => [String(row[0]), {row:i+2, status:row[6]}]));
  const result = [];
  for (const response of form.getResponses()) {
    const id = response.getId();
    if (known.has(id) && known.get(id).status !== 'pending') continue;
    const values = response.getItemResponses();
    if (values.length !== 4 || values.some((v,i) => v.getItem().getTitle() !== OTT.questions[i]))
      throw Error('response schema');
    const answers = values.map(v => String(v.getResponse()));
    const row = {response_id:id, created_at:response.getTimestamp().toISOString(),
      email:response.getRespondentEmail(), identity:answers[0], name:answers[1],
      repository:answers[2], password:answers[3]};
    if (!known.has(id)) {
      if (sheet.getLastRow() >= 20000) throw Error('queue full');
      sheet.appendRow([id,row.created_at,row.email,row.identity,row.name,row.repository,'pending',''].map(safeCell_));
    }
    result.push(row);
    if (result.length === 10) break;
  }
  SpreadsheetApp.flush();
  return {registrations:result};
}

function pullReset_() {
  const form = form_(true);
  const sheet = sheet_(OTT.registration, OTT.resetTab, OTT.resetHeader);
  const known = new Map(queueRows_(sheet,6).map(row => [String(row[0]), row[4]]));
  const result = [];
  for (const response of form.getResponses()) {
    const id = response.getId();
    if (known.has(id) && known.get(id) !== 'pending') continue;
    const values = response.getItemResponses();
    const answers = new Map();
    for (const value of values) {
      const title = value.getItem().getTitle();
      if (!OTT.resetQuestions.includes(title) || answers.has(title)) throw Error('reset schema');
      answers.set(title, String(value.getResponse()));
    }
    const selection = answers.get(OTT.resetQuestions[1]);
    // Responses submitted before the form upgrade retain the two old items.
    const legacy = !answers.has(OTT.resetQuestions[1]) && !answers.has(OTT.resetQuestions[3]);
    const changes = new Map(OTT.resetChoices.map((label,i)=>[label,['password','repository','both'][i]]));
    const change = legacy ? 'password' : (changes.get(selection) || 'invalid');
    const row = {response_id:id, created_at:response.getTimestamp().toISOString(),
      email:response.getRespondentEmail(), identity:answers.get(OTT.resetQuestions[0]) || '', change,
      password:answers.get(OTT.resetQuestions[2]) || '', repository:answers.get(OTT.resetQuestions[3]) || ''};
    if (!known.has(id)) {
      if (sheet.getLastRow() >= 20000) throw Error('queue full');
      sheet.appendRow([id,row.created_at,row.email,row.identity,'pending',''].map(safeCell_));
    }
    result.push(row);
    if (result.length === 10) break;
  }
  SpreadsheetApp.flush();
  return {resets:result};
}

function ack_(receipts, reset = false) {
  if (!Array.isArray(receipts) || receipts.length > 50) throw Error('ack size');
  const form = form_(reset);
  const sheet = sheet_(OTT.registration, reset ? OTT.resetTab : OTT.registrationTab, reset ? OTT.resetHeader : OTT.header);
  const rows = new Map(queueRows_(sheet, reset ? 6 : 8).map((row,i) => [String(row[0]), {row:i+2}]));
  const existing = new Set(form.getResponses().map(r => r.getId()));
  const acknowledged = [];
  for (const receipt of receipts) {
    const record = rows.get(receipt.response_id);
    if (!record || !['accepted','rejected'].includes(receipt.status)
        || typeof receipt.message !== 'string' || receipt.message.length > 500) throw Error('ack');
    // Delete only named responses after Master has committed its receipt.
    // If Google stopped after deletion, the queued metadata makes retry safe.
    if (existing.has(receipt.response_id)) form.deleteResponse(receipt.response_id);
    sheet.getRange(record.row,reset ? 5 : 7,1,2).setValues([[receipt.status,safeCell_(receipt.message)]]);
    SpreadsheetApp.flush();
    acknowledged.push(receipt.response_id);
  }
  return {acknowledged};
}

function ranking_(data) {
  if (!Array.isArray(data.rows) || data.rows.length > 4000 || !Number.isFinite(data.generated_at)
      || Math.abs(Date.now()/1000-data.generated_at)>600) throw Error('ranking');
  for (const row of data.rows) {
    if (!Array.isArray(row) || row.length!==9 || !Number.isSafeInteger(row[0]) || row[0]<1
        || typeof row[1]!=='string' || row[1].length>64 || typeof row[2]!=='string' || row[2].length>100
        || !Number.isFinite(row[3]) || !row.slice(4,8).every(n=>Number.isSafeInteger(n)&&n>=0)
        || row[4]!==row[5]+row[6]+row[7] || typeof row[8]!=='string' || row[8].length>50)
      throw Error('rank row');
  }
  const properties = PropertiesService.getScriptProperties();
  const previous = Number(properties.getProperty('OTTV2_RANK_TIME')||0);
  if (data.generated_at < previous) throw Error('old ranking');
  const sheet = sheet_(OTT.ranking, OTT.rankingTab, OTT.rankHeader);
  const count = Math.max(sheet.getLastRow()-1, data.rows.length);
  if (sheet.getMaxRows()<data.rows.length+1) sheet.insertRowsAfter(sheet.getMaxRows(), data.rows.length+1-sheet.getMaxRows());
  if (count) {
    const rows = data.rows.map(row => row.map(v => typeof v==='string'?safeCell_(v):v));
    while (rows.length<count) rows.push(Array(9).fill(''));
    sheet.getRange(2,1,count,9).setValues(rows);
  }
  sheet.getRange('K1:L2').setValues([['Cập nhật lúc',new Date(data.generated_at*1000).toISOString()],
    ['Số tài khoản',data.rows.length]]);
  SpreadsheetApp.flush();
  properties.setProperty('OTTV2_RANK_TIME',String(data.generated_at));
  return {published:data.rows.length};
}
