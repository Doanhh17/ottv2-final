/* Owner-only editor action. Preserve the existing form ID, responses and
 * Verified email setting. Never exposed as a network bridge action. */
function preparePasswordForm() {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const id = properties.getProperty('OTTV2_PASSWORD_FORM') || OTT.resetForm;
    if (id && OTT.resetForm && id !== OTT.resetForm) throw Error('Kiểm tra lại ID Form trước khi nâng cấp.');
    const form = id ? FormApp.openById(id) : FormApp.create('OTTv2 — Đổi / quên mật khẩu hoặc link GitHub');
    const items = form.getItems();
    const titles = items.map(item=>item.getTitle());
    // Permit a known subset too: an interrupted upgrade can be run again.
    if (new Set(titles).size !== titles.length || titles.some(title=>!OTT.resetQuestions.includes(title)))
      throw Error('Form có câu hỏi khác mẫu; dừng để tránh thay nhầm.');
    if (!id) {
      form.setCollectEmail(true);
      form.setAllowResponseEdits(false);
      form.setLimitOneResponsePerUser(false);
      form.setShowLinkToRespondAgain(false);
    }
    const byTitle = new Map(items.map(item=>[item.getTitle(),item]));
    const student = byTitle.has(OTT.resetQuestions[0]) ? byTitle.get(OTT.resetQuestions[0]).asTextItem() : form.addTextItem().setTitle(OTT.resetQuestions[0]);
    student.setRequired(true);
    const password = byTitle.has(OTT.resetQuestions[2]) ? byTitle.get(OTT.resetQuestions[2]).asTextItem() : form.addTextItem().setTitle(OTT.resetQuestions[2]);
    password.setRequired(false).setHelpText('Chỉ điền khi chọn đổi mật khẩu hoặc đổi cả hai. Chỉ đổi link GitHub thì để trống. Không dùng mật khẩu Google.')
      .setValidation(FormApp.createTextValidation().requireTextMatchesPattern('^[\\s\\S]{12,256}$')
        .setHelpText('Mật khẩu riêng cho OTTv2 cần 12–256 ký tự; chỉ đổi GitHub thì để trống.').build());
    const change = byTitle.has(OTT.resetQuestions[1]) ? byTitle.get(OTT.resetQuestions[1]).asMultipleChoiceItem() : form.addMultipleChoiceItem().setTitle(OTT.resetQuestions[1]);
    change.setChoiceValues(OTT.resetChoices).setRequired(true);
    const repository = byTitle.has(OTT.resetQuestions[3]) ? byTitle.get(OTT.resetQuestions[3]).asTextItem() : form.addTextItem().setTitle(OTT.resetQuestions[3]);
    repository.setRequired(false).setHelpText('Chỉ điền khi đổi link GitHub hoặc đổi cả hai. Dạng https://github.com/ten-nguoi-dung/ten-kho, hệ thống chỉ đọc main.py ở thư mục gốc của repo công khai (Public).');
    if (change.getIndex() !== 1) form.moveItem(change.getIndex(),1);
    form.setTitle('OTTv2 — Đổi / quên mật khẩu hoặc link GitHub');
    form.setDescription('Dùng đúng email Google trường (@vnu.edu.vn) và MSSV đã đăng ký. Chọn nội dung cần đổi, chỉ điền ô tương ứng; muốn đổi cả hai thì điền cả mật khẩu và link. Máy quản lý nhận yêu cầu mỗi khoảng 1 phút khi đang chạy; đông người hoặc mất mạng có thể lâu hơn. Đổi link chỉ lưu nơi lấy mã mới: đăng nhập rồi bấm Cập nhật bot; không bỏ qua thời gian chờ 3 phút. Đổi mật khẩu thu hồi các phiên cũ; chỉ đổi link không thay mật khẩu. Không đăng ký lại tài khoản để đổi thông tin.');
    form.setConfirmationMessage('Đã gửi yêu cầu. Chờ máy quản lý xử lý; email và MSSV phải khớp tài khoản. Đổi mật khẩu thì đăng nhập lại. Đổi link GitHub thì vào Bot của tôi kiểm link và bấm Cập nhật bot khi hết thời gian chờ. Nếu chưa được, liên hệ ban tổ chức và không gửi mật khẩu.');
    properties.setProperty('OTTV2_PASSWORD_FORM',form.getId());
    const result = {form_id:form.getId(), edit_url:form.getEditUrl(), public_url:form.getPublishedUrl(),
      items:form.getItems().map(item=>item.getTitle()), responses_preserved:true};
    console.log(JSON.stringify(result));
    return result;
  } finally {
    if (lock.hasLock()) lock.releaseLock();
  }
}

function upgradeAccountChangeForm() {
  return preparePasswordForm();
}

// Run only after the owner visually reviews Verified mode in Forms settings.
function confirmPasswordEmailReviewed() {
  PropertiesService.getScriptProperties().setProperty('OTTV2_RESET_VERIFIED_EMAIL', 'reviewed-verified');
  console.log(JSON.stringify(checkSetup()));
}
