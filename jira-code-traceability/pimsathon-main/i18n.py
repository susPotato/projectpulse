"""Runtime UI translation: English / Japanese / Vietnamese.

``tr(key, **kwargs)`` returns the string for the current language (falling
back to English, then the key itself so a missing entry is still visible
instead of crashing). ``.format(**kwargs)`` is applied when placeholders are
passed, so callers can do e.g. ``tr("composer.attachments", n=3)``.

Persistent, long-lived widgets (the main window chrome, the tabs, the
sidebar, the composer, ...) must reflect a language change immediately, so
they register a zero-arg callback via :func:`on_language_changed` that
re-applies ``tr()`` to their own text; the callback runs once right away and
again every time the language changes. Transient dialogs (Settings, Skills,
Flow, Permission...) are rebuilt from scratch each time they are opened, so
they simply call ``tr()`` while constructing their widgets and need no
registration.
"""
from __future__ import annotations

from typing import Callable, Dict, List

LANGUAGES: Dict[str, str] = {"en": "English", "ja": "日本語", "vi": "Tiếng Việt"}
# Short codes shown in the compact top-bar switcher (Settings keeps the full names above).
LANGUAGE_SHORT: Dict[str, str] = {"en": "EN", "ja": "JP", "vi": "VN"}
DEFAULT_LANGUAGE = "vi"

_current = DEFAULT_LANGUAGE
_listeners: List[Callable[[], None]] = []

# key -> {"en": ..., "ja": ..., "vi": ...}
STRINGS: Dict[str, Dict[str, str]] = {
    # ---- login_dialog.py: startup login / bootstrap / offline ----
    "login.title": {"en": "Sign in", "ja": "サインイン", "vi": "Đăng nhập"},
    "login.header": {"en": "Cowork-Local BamBOO", "ja": "Cowork-Local BamBOO", "vi": "Cowork-Local BamBOO"},
    "login.account": {"en": "Account", "ja": "アカウント", "vi": "Tài khoản"},
    "login.code": {"en": "Access code", "ja": "アクセスコード", "vi": "Mã truy cập"},
    "login.department": {"en": "Department (optional)", "ja": "部署（任意）", "vi": "Phòng ban (không bắt buộc)"},
    "login.department_placeholder": {
        "en": "e.g. FA.PDS — groups you automatically", "ja": "例: FA.PDS — 自動でグループ分けされます",
        "vi": "vd: FA.PDS — sẽ tự động xếp vào nhóm tương ứng"},
    "login.login_btn": {"en": "Log in", "ja": "ログイン", "vi": "Đăng nhập"},
    "login.exit_btn": {"en": "Exit", "ja": "終了", "vi": "Thoát"},
    "login.err_invalid": {
        "en": "Invalid account or access code.", "ja": "アカウントまたはアクセスコードが無効です。",
        "vi": "Tài khoản hoặc mã truy cập không đúng."},
    "login.err_admin_exists": {
        "en": "An Admin account already exists for this shared folder — the app has exactly one. Log in with an account issued by the Admin instead.",
        "ja": "この共有フォルダには既に管理者アカウントが存在します（管理者は1人のみ）。管理者から発行されたアカウントでログインしてください。",
        "vi": "Thư mục dùng chung này đã có tài khoản Admin — app chỉ có duy nhất 1 Admin. Hãy đăng nhập bằng tài khoản do Admin cấp."},
    "login.err_missing_fields": {
        "en": "Enter both a shared folder path and an account name.",
        "ja": "共有フォルダのパスとアカウント名の両方を入力してください。",
        "vi": "Nhập đường dẫn thư mục chia sẻ và tên tài khoản."},
    "login.err_shared_dir": {
        "en": "Could not create the shared folder: {error}",
        "ja": "共有フォルダを作成できませんでした: {error}",
        "vi": "Không tạo được thư mục chia sẻ: {error}"},
    "login.bootstrap_hint": {
        "en": "No accounts exist yet. Choose a shared folder (a network share or a "
              "locally-synced OneDrive folder) and create the first Admin account.",
        "ja": "アカウントがまだありません。共有フォルダ（ネットワーク共有、または同期済みの "
              "OneDrive フォルダ）を選び、最初の管理者アカウントを作成してください。",
        "vi": "Chưa có tài khoản nào. Chọn một thư mục chia sẻ (network share hoặc thư mục "
              "OneDrive đã đồng bộ trên máy) và tạo tài khoản Admin đầu tiên."},
    "login.shared_dir": {"en": "Shared folder", "ja": "共有フォルダ", "vi": "Thư mục chia sẻ"},
    "login.browse": {"en": "Browse…", "ja": "参照…", "vi": "Chọn…"},
    "login.create_admin": {
        "en": "Create Admin account", "ja": "管理者アカウントを作成", "vi": "Tạo tài khoản Admin"},
    "login.code_shown_title": {"en": "Admin account created", "ja": "管理者アカウントを作成しました",
                                "vi": "Đã tạo tài khoản Admin"},
    "login.code_shown_body": {
        "en": "Account: {username}\nAccess code: {code}\n\nSave this code now — it will "
              "not be shown again. You are now logged in.",
        "ja": "アカウント: {username}\nアクセスコード: {code}\n\n今すぐこのコードを保存してくださ"
              "い — 二度と表示されません。ログインしました。",
        "vi": "Tài khoản: {username}\nMã truy cập: {code}\n\nHãy lưu lại mã này ngay — mã sẽ "
              "không hiển thị lại lần nào nữa. Bạn đã đăng nhập."},
    "login.unreachable": {
        "en": "Can't reach the shared folder:\n{path}", "ja": "共有フォルダに到達できません:\n{path}",
        "vi": "Không truy cập được thư mục chia sẻ:\n{path}"},
    "login.offline_hint": {
        "en": "Last successful login on this machine: {username} ({role}).",
        "ja": "このマシンでの最後の正常なログイン: {username} ({role})。",
        "vi": "Lần đăng nhập thành công gần nhất trên máy này: {username} ({role})."},
    "login.offline_btn": {"en": "Continue offline as {role}", "ja": "{role} としてオフラインで続行",
                           "vi": "Tiếp tục offline với vai trò {role}"},
    "login.no_offline_cache": {
        "en": "No previous successful login on this machine — contact your Admin.",
        "ja": "このマシンでの過去のログイン履歴がありません — 管理者に連絡してください。",
        "vi": "Chưa có lượt đăng nhập thành công nào trên máy này — liên hệ Admin."},
    "login.retry_btn": {"en": "Retry", "ja": "再試行", "vi": "Thử lại"},

    # ---- accounts_tab.py: Monitoring -> Accounts panel (Admin/Sub-admin) --
    "accounts.edit_title": {"en": "Edit account", "ja": "アカウントを編集", "vi": "Sửa tài khoản"},
    "accounts.add_title": {"en": "Add account", "ja": "アカウントを追加", "vi": "Thêm tài khoản"},
    "accounts.f_username": {"en": "Account", "ja": "アカウント", "vi": "Tài khoản"},
    "accounts.f_display_name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "accounts.f_email": {"en": "Email", "ja": "メール", "vi": "Email"},
    "accounts.f_email_placeholder": {
        "en": "name@company.com (optional)", "ja": "name@company.com（任意）",
        "vi": "name@company.com (không bắt buộc)"},
    "accounts.f_role": {"en": "Role", "ja": "役割", "vi": "Vai trò"},
    "accounts.f_department": {"en": "Department", "ja": "部門", "vi": "Bộ phận"},
    "accounts.f_group": {"en": "Group", "ja": "グループ", "vi": "Nhóm"},
    "accounts.f_group_name": {"en": "Group name", "ja": "グループ名", "vi": "Tên nhóm"},
    "accounts.no_group": {"en": "— No group —", "ja": "— グループなし —", "vi": "— Không có nhóm —"},
    "accounts.role.admin": {"en": "Admin", "ja": "管理者", "vi": "Admin"},
    "accounts.role.subadmin": {"en": "Sub-admin", "ja": "サブ管理者", "vi": "Sub-admin"},
    "accounts.role.user": {"en": "User", "ja": "ユーザー", "vi": "User"},
    "accounts.no_shared_dir": {
        "en": "No shared folder configured — set one in Settings to manage accounts.",
        "ja": "共有フォルダが設定されていません — 設定でアカウント管理用のフォルダを指定してください。",
        "vi": "Chưa cấu hình thư mục chia sẻ — thiết lập trong Settings để quản lý tài khoản."},
    "accounts.shared_dir_hint": {"en": "Shared folder: {path}", "ja": "共有フォルダ: {path}",
                                  "vi": "Thư mục chia sẻ: {path}"},
    "accounts.ungrouped": {"en": "Ungrouped", "ja": "未分類", "vi": "Chưa có nhóm"},
    "accounts.filter_all_groups": {"en": "All groups", "ja": "すべてのグループ", "vi": "Tất cả nhóm"},
    "accounts.delete_title": {"en": "Delete account", "ja": "アカウントを削除", "vi": "Xóa tài khoản"},
    "accounts.delete_confirm": {"en": "Delete account '{username}'?", "ja": "アカウント「{username}」を削"
                                 "除しますか？", "vi": "Xóa tài khoản '{username}'?"},
    "accounts.new_group_title": {"en": "New group", "ja": "新しいグループ", "vi": "Nhóm mới"},
    "accounts.add_btn": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "accounts.edit_btn": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "accounts.delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "accounts.generate_code_btn": {"en": "Generate code", "ja": "コード発行", "vi": "Tạo mã"},
    "accounts.new_group_btn": {"en": "New group", "ja": "新しいグループ", "vi": "Nhóm mới"},
    "accounts.drag_move_hint": {
        "en": "Drag an account onto a group to move it there.",
        "ja": "アカウントをグループにドラッグすると移動できます。",
        "vi": "Kéo tài khoản thả vào một nhóm để di chuyển đến đó."},
    "accounts.err_admin_exists": {
        "en": "An Admin account already exists — the app has exactly one.",
        "ja": "管理者アカウントは既に存在します（1人のみ）。",
        "vi": "Đã có tài khoản Admin — app chỉ có duy nhất 1 Admin."},
    "accounts.search_placeholder": {
        "en": "Search accounts (or type a question and press )…",
        "ja": "アカウント検索（質問を入力しても可）…",
        "vi": "Tìm tài khoản (hoặc gõ câu hỏi rồi bấm )…"},
    "accounts.ai_search_btn": {"en": "AI", "ja": "AI", "vi": "AI"},
    "accounts.ai_search_tooltip": {
        "en": "AI turns your question into a search keyword (e.g. \"who in CAE has no department?\").",
        "ja": "質問をAIが検索キーワードに変換します。",
        "vi": "AI chuyển câu hỏi của bạn thành từ khóa tìm kiếm (vd: \"ai trong CAE chưa có phòng ban?\")."},
    "accounts.excel_template_btn": {
        "en": "Excel template", "ja": "Excelテンプレート", "vi": "Mẫu Excel"},
    "accounts.excel_import_btn": {
        "en": "Import Excel", "ja": "Excel取り込み", "vi": "Nhập từ Excel"},
    "accounts.excel_imported": {
        "en": "Created {n} account(s).", "ja": "{n} 件のアカウントを作成しました。",
        "vi": "Đã tạo {n} tài khoản."},
    "accounts.excel_codes_saved": {
        "en": "Access codes saved to: {path}", "ja": "アクセスコードの保存先: {path}",
        "vi": "Mã truy cập đã lưu tại: {path}"},
    "accounts.usage_title": {"en": "Usage & Cost by account", "ja": "アカウント別の使用量とコスト",
                              "vi": "Sử dụng & Chi phí theo tài khoản"},
    "accounts.period.day": {"en": "Day", "ja": "日", "vi": "Ngày"},
    "accounts.period.week": {"en": "Week", "ja": "週", "vi": "Tuần"},
    "accounts.period.month": {"en": "Month", "ja": "月", "vi": "Tháng"},
    "accounts.period.year": {"en": "Year", "ja": "年", "vi": "Năm"},
    "accounts.col_name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "accounts.col_account": {"en": "Account", "ja": "アカウント", "vi": "Tài khoản"},
    "accounts.col_machine": {"en": "Machine", "ja": "マシン", "vi": "Máy"},
    "accounts.col_department": {"en": "Department", "ja": "部門", "vi": "Bộ phận"},
    "accounts.col_tokens": {"en": "Tokens", "ja": "トークン", "vi": "Token"},
    "accounts.col_cost": {"en": "Cost", "ja": "コスト", "vi": "Chi phí"},

    # ---- app.py: top bar, tabs, toasts, tray ------------------------
    "app.logo": {"en": "Cowork-Local BamBOO", "ja": "Cowork-Local BamBOO", "vi": "Cowork-Local BamBOO"},
    "app.provider": {"en": "Provider:", "ja": "プロバイダー:", "vi": "Nhà cung cấp:"},
    "app.language": {"en": "Language:", "ja": "言語:", "vi": "Ngôn ngữ:"},
    "app.settings": {"en": "Settings", "ja": "設定", "vi": "Cài đặt"},
    "app.tab.dashboard": {"en": "Dashboard", "ja": "Dashboard", "vi": "Dashboard"},
    "app.tab.schedule": {"en": "Schedule Task", "ja": "Schedule Task", "vi": "Schedule Task"},
    "app.tab.cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "app.tab.code": {"en": "Code", "ja": "Code", "vi": "Code"},
    "app.tab.structure": {"en": "GraphRAG", "ja": "GraphRAG", "vi": "GraphRAG"},
    "app.tab.workspace": {"en": "Workspace", "ja": "ワークスペース", "vi": "Workspace"},
    "app.tab.monitoring": {"en": "Monitoring", "ja": "モニタリング", "vi": "Giám sát"},
    "app.nav.collapse_tooltip": {"en": "Collapse menu to icons only", "ja": "メニューをアイコンのみに折りたたむ", "vi": "Thu gọn menu về icon"},
    "app.nav.expand_tooltip": {"en": "Expand menu", "ja": "メニューを展開", "vi": "Mở rộng menu"},
    "app.nav.menu_label": {"en": "MENU", "ja": "MENU", "vi": "MENU"},

    # ---- workspace_tab.py (Projects — Claude-Projects style) -----------
    "workspace.header": {"en": "Workspace — Projects", "ja": "ワークスペース — プロジェクト", "vi": "Workspace — Projects"},
    "workspace.tab_cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "workspace.tab_graphrag": {"en": "GraphRAG", "ja": "GraphRAG", "vi": "GraphRAG"},
    "workspace.tab_project": {"en": "Project", "ja": "プロジェクト", "vi": "Project"},
    "workspace.tab_folder": {"en": "Folder", "ja": "フォルダ", "vi": "Thư mục"},
    "folder.path_placeholder": {
        "en": "Folder path", "ja": "フォルダのパス", "vi": "Đường dẫn thư mục"},
    "folder.open_folder": {"en": "Open folder", "ja": "フォルダを開く", "vi": "Mở thư mục"},
    "folder.save": {"en": "Save", "ja": "保存", "vi": "Lưu"},
    "folder.open_external": {
        "en": "Open externally", "ja": "外部で開く", "vi": "Mở bằng app ngoài"},
    "folder.preview": {"en": "Preview", "ja": "プレビュー", "vi": "Xem trước"},
    "folder.edit": {"en": "Edit", "ja": "編集", "vi": "Chỉnh sửa"},
    "folder.select_file": {
        "en": "Select a file in the tree to view or edit it.",
        "ja": "ツリーでファイルを選択して表示・編集します。",
        "vi": "Chọn một tệp trong cây thư mục để xem hoặc chỉnh sửa."},
    "folder.binary_file": {
        "en": "Binary or very large file — open it externally to view.",
        "ja": "バイナリまたは非常に大きいファイルです — 外部で開いて表示してください。",
        "vi": "Tệp nhị phân hoặc quá lớn — mở bằng app ngoài để xem."},
    "folder.converting": {
        "en": "Rendering document… (converting to PDF via LibreOffice)",
        "ja": "ドキュメントを表示中…（LibreOffice で PDF に変換しています）",
        "vi": "Đang hiển thị tài liệu… (chuyển sang PDF bằng LibreOffice)"},
    "folder.doc_unreadable": {
        "en": "Could not extract text ({note}). Open it externally for the full document.",
        "ja": "テキストを抽出できませんでした ({note})。完全な文書は外部で開いてください。",
        "vi": "Không trích xuất được nội dung ({note}). Mở bằng app ngoài để xem đầy đủ."},
    "folder.saved": {"en": "Saved {name}", "ja": "{name} を保存しました", "vi": "Đã lưu {name}"},
    "folder.ai_edit": {"en": "AI Edit", "ja": "AI 編集", "vi": "AI Edit"},
    "folder.ai_edit_tooltip": {
        "en": "Edit the open file with AI (uses the Cowork conversation context)",
        "ja": "AI で開いているファイルを編集（Cowork の会話コンテキストを利用）",
        "vi": "Dùng AI chỉnh sửa file đang mở (dùng ngữ cảnh hội thoại Cowork)"},
    "folder.ai_placeholder": {
        "en": "Describe the edit… (e.g. add error handling)",
        "ja": "編集内容を入力…（例: エラー処理を追加）",
        "vi": "Mô tả chỉnh sửa… (vd: thêm xử lý lỗi)"},
    "folder.ai_send": {"en": "Send", "ja": "送信", "vi": "Gửi"},
    "folder.ai_no_file": {
        "en": "Open a text/code file in Edit mode first.",
        "ja": "先にテキスト/コードファイルを編集モードで開いてください。",
        "vi": "Hãy mở một file text/code ở chế độ Edit trước."},
    "folder.ai_applied": {
        "en": "✓ Applied the edit — review it and Save.",
        "ja": "✓ 編集を適用しました — 確認して保存してください。",
        "vi": "✓ Đã áp dụng chỉnh sửa — kiểm tra rồi Lưu."},
    "folder.ai_empty": {
        "en": "(the model didn't return an edited file)",
        "ja": "(モデルは編集後のファイルを返しませんでした)",
        "vi": "(model không trả về file đã chỉnh sửa)"},
    "folder.ai_error": {
        "en": "AI edit failed: {err}", "ja": "AI 編集に失敗しました: {err}",
        "vi": "AI edit thất bại: {err}"},
    "folder.ai_running": {
        "en": "AI is editing {name}… (keeps running while you do other things)",
        "ja": "AI が {name} を編集中…（他の作業をしていても継続します）",
        "vi": "AI đang chỉnh sửa {name}… (vẫn chạy tiếp khi bạn làm việc khác)"},
    "folder.ai_done": {
        "en": "AI edit finished for {name} — review it in the Folder tab.",
        "ja": "{name} の AI 編集が完了しました — Folder タブで確認してください。",
        "vi": "AI edit xong cho {name} — kiểm tra ở tab Folder."},
    "folder.ai_status_running": {
        "en": "processing…", "ja": "処理中…", "vi": "đang xử lí…"},
    "folder.ai_status_done": {
        "en": "done", "ja": "完了", "vi": "xong"},
    "folder.ai_planning": {
        "en": "Planning…", "ja": "計画中…", "vi": "Đang lập kế hoạch…"},
    "folder.ai_apply": {"en": "Apply", "ja": "適用", "vi": "Áp dụng"},
    "folder.ai_discard": {"en": "Discard", "ja": "破棄", "vi": "Hủy"},
    "folder.ai_proposed": {
        "en": "Proposed changes (review)", "ja": "変更案（確認）",
        "vi": "Thay đổi đề xuất (xem lại)"},
    "folder.ai_review_hint": {
        "en": "Review the diff, then Apply or Discard.",
        "ja": "差分を確認してから、適用または破棄してください。",
        "vi": "Xem lại diff rồi bấm Áp dụng hoặc Hủy."},
    "folder.ai_proposed_status": {
        "en": "AI proposed an edit for {name} — review & Apply.",
        "ja": "{name} の編集案が出ました — 確認して適用してください。",
        "vi": "AI đề xuất chỉnh sửa {name} — xem lại & Áp dụng."},
    "folder.ai_discarded": {
        "en": "Discarded — the file was not changed.",
        "ja": "破棄しました — ファイルは変更されていません。",
        "vi": "Đã hủy — file không bị thay đổi."},
    "folder.ai_new_file": {"en": "a new file", "ja": "新規ファイル", "vi": "file mới"},
    "folder.ai_proposed_new": {
        "en": "Proposed NEW file: {name} (review)",
        "ja": "新規ファイルの提案: {name}（確認）",
        "vi": "Đề xuất tạo file MỚI: {name} (xem lại)"},
    "folder.ai_created": {
        "en": "Created {name}", "ja": "{name} を作成しました", "vi": "Đã tạo {name}"},
    "folder.ai_image_confirm_title": {
        "en": "Confirm image change", "ja": "画像変更の確認", "vi": "Xác nhận sửa ảnh"},
    "folder.ai_image_confirm": {
        "en": "This edit replaces one or more images in the slide. Proceed?",
        "ja": "この編集はスライド内の画像を置き換えます。実行しますか？",
        "vi": "Chỉnh sửa này sẽ thay ảnh trong slide. Tiếp tục?"},
    "folder.ai_image_declined": {
        "en": "Image change cancelled.", "ja": "画像の変更をキャンセルしました。",
        "vi": "Đã hủy thay đổi ảnh."},
    "folder.ai_image_confirm_gen": {
        "en": "This will GENERATE image(s) with the AI model and save them into the folder. Proceed?",
        "ja": "AI モデルで画像を生成してフォルダに保存します。実行しますか？",
        "vi": "Sẽ TẠO ảnh bằng model AI và lưu vào thư mục. Tiếp tục?"},
    "folder.ai_image_plan": {
        "en": "Will generate these illustration image(s):",
        "ja": "以下のイラスト画像を生成します:",
        "vi": "Sẽ tạo các ảnh minh họa sau:"},
    "folder.ai_generating": {
        "en": "Generating image(s)…", "ja": "画像を生成中…", "vi": "Đang tạo ảnh…"},
    "folder.ai_image_created": {
        "en": "Generated image {name}", "ja": "画像 {name} を生成しました",
        "vi": "Đã tạo ảnh {name}"},
    "folder.ai_image_failed": {
        "en": "Image generation failed: {err}", "ja": "画像生成に失敗しました: {err}",
        "vi": "Tạo ảnh thất bại: {err}"},
    "folder.ai_model_label": {"en": "Model:", "ja": "モデル:", "vi": "Model:"},
    "folder.ai_model_auto": {
        "en": "(auto — provider default)", "ja": "(自動 — 既定モデル)",
        "vi": "(tự động — model mặc định)"},
    "folder.ai_image_suggest": {
        "en": "💡 Tip: pick model '{model}' above for image generation.",
        "ja": "💡 画像生成には上のモデル '{model}' を選ぶのがおすすめです。",
        "vi": "💡 Gợi ý: chọn model '{model}' ở trên để tạo ảnh."},
    "folder.ai_image_suggest_all": {
        "en": "💡 This request involves images. Image-capable models found on other providers:",
        "ja": "💡 このリクエストは画像を含みます。他プロバイダーで見つかった画像対応モデル:",
        "vi": "💡 Yêu cầu này liên quan đến ảnh. Model tạo ảnh tìm thấy ở các provider khác:"},
    "folder.ai_image_none": {
        "en": "💡 This request involves images, but no image-capable model was found on any configured provider.",
        "ja": "💡 このリクエストは画像を含みますが、設定済みのどのプロバイダーにも画像対応モデルが見つかりませんでした。",
        "vi": "💡 Yêu cầu này liên quan đến ảnh, nhưng không tìm thấy model tạo ảnh ở provider nào đã cấu hình."},
    "folder.ai_image_use_selected": {
        "en": "💡 No dedicated image model found — will use your selected model '{model}' to generate images.",
        "ja": "💡 専用の画像モデルが見つかりません — 選択中のモデル '{model}' で画像を生成します。",
        "vi": "💡 Không tìm thấy model tạo ảnh chuyên biệt — sẽ dùng model bạn đã chọn '{model}' để tạo ảnh."},
    "folder.ai_queued": {
        "en": "⏳ Queued (#{n}) — runs after the current edit.",
        "ja": "⏳ キューに追加 (#{n}) — 現在の編集の後に実行します。",
        "vi": "⏳ Đã thêm vào hàng đợi (#{n}) — chạy sau lệnh hiện tại."},
    "folder.ai_queue_count": {
        "en": "{n} queued", "ja": "{n} 件待機中", "vi": "{n} đang chờ"},
    "terminal.title": {"en": "Terminal", "ja": "ターミナル", "vi": "Terminal"},
    "terminal.run": {"en": "Run", "ja": "実行", "vi": "Chạy"},
    "terminal.placeholder": {
        "en": "Type a command and press Enter…", "ja": "コマンドを入力して Enter…",
        "vi": "Nhập lệnh rồi nhấn Enter…"},
    "terminal.expand_tooltip": {
        "en": "Expand terminal", "ja": "ターミナルを開く", "vi": "Mở terminal"},
    "terminal.collapse_tooltip": {
        "en": "Collapse terminal", "ja": "ターミナルを閉じる", "vi": "Thu gọn terminal"},
    "terminal.busy": {
        "en": "[a command is still running]", "ja": "[コマンドがまだ実行中です]",
        "vi": "[đang chạy một lệnh khác]"},
    "terminal.cd_error": {
        "en": "cd: no such directory: {path}", "ja": "cd: ディレクトリがありません: {path}",
        "vi": "cd: không có thư mục: {path}"},
    "terminal.launch_error": {
        "en": "[failed to launch the shell]", "ja": "[シェルの起動に失敗しました]",
        "vi": "[không khởi chạy được shell]"},
    "terminal.exit": {
        "en": "[process exited with code {code}]", "ja": "[プロセス終了 コード {code}]",
        "vi": "[tiến trình kết thúc, mã {code}]"},
    "folder.save_error": {
        "en": "Save failed: {err}", "ja": "保存に失敗しました: {err}", "vi": "Lưu thất bại: {err}"},

    "workspace.hint": {
        "en": ("Group chats into projects. Every thread in a project follows the shared "
               "Instructions, works inside the project's own sandbox folder, and auto-reads "
               "files placed at that folder's root (project knowledge)."),
        "ja": ("チャットをプロジェクトにまとめます。プロジェクト内の各スレッドは共有の指示に従い、"
               "プロジェクト専用のサンドボックスフォルダ内で動作し、そのルートに置かれたファイル"
               "（プロジェクトナレッジ）を自動的に読み込みます。"),
        "vi": ("Gom các cuộc chat thành project. Mọi thread trong một project tuân theo phần "
               "Instructions chung, làm việc trong thư mục sandbox riêng của project, và tự đọc "
               "các file đặt ở gốc thư mục đó (project knowledge)."),
    },
    "workspace.new_project": {"en": "New project", "ja": "新規プロジェクト", "vi": "Project mới"},
    "workspace.delete": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "workspace.delete_confirm": {
        "en": "Delete project “{name}”? Its conversations and files are kept (threads move to General).",
        "ja": "プロジェクト「{name}」を削除しますか？会話とファイルは保持されます（スレッドは General へ移動）。",
        "vi": "Xóa project “{name}”? Hội thoại và file vẫn được giữ (thread chuyển về General).",
    },
    "workspace.deleted": {"en": "Deleted project {name}.", "ja": "プロジェクト {name} を削除しました。", "vi": "Đã xóa project {name}."},
    "workspace.conversation_project_missing": {
        "en": "This conversation's project no longer exists — it can't be opened.",
        "ja": "この会話のプロジェクトは既に存在しないため開けません。",
        "vi": "Project của hội thoại này không còn tồn tại — không thể mở."},
    "workspace.name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "workspace.description": {"en": "Description", "ja": "説明", "vi": "Mô tả"},
    "workspace.instructions": {"en": "Instructions (shared project context)", "ja": "指示（プロジェクト共有コンテキスト）", "vi": "Instructions (ngữ cảnh chung của project)"},
    "workspace.instructions_placeholder": {
        "en": "e.g. \"All answers in Vietnamese. We are building the X reporting tool; always follow the naming rules …\"",
        "ja": "例:「回答はすべて日本語で。X レポートツールを開発中。命名規則に従うこと …」",
        "vi": "vd: \"Trả lời bằng tiếng Việt. Team đang xây tool báo cáo X; luôn theo quy tắc đặt tên …\"",
    },
    "workspace.browse": {"en": "Change folder…", "ja": "フォルダ変更…", "vi": "Đổi thư mục…"},
    "workspace.browse_tooltip": {
        "en": "Choose the project's workspace folder (agent sandbox + shared knowledge root)",
        "ja": "プロジェクトのワークスペースフォルダを選択（エージェントのサンドボックス＋共有ナレッジのルート）",
        "vi": "Chọn thư mục workspace của project (sandbox của agent + gốc chứa knowledge chung)",
    },
    "workspace.open_folder": {"en": "Open folder", "ja": "フォルダを開く", "vi": "Mở thư mục"},
    "workspace.save": {"en": "Save project", "ja": "プロジェクトを保存", "vi": "Lưu project"},
    "workspace.saved": {"en": "Saved project {name}.", "ja": "プロジェクト {name} を保存しました。", "vi": "Đã lưu project {name}."},
    "workspace.threads": {"en": "Conversations in this project", "ja": "このプロジェクトの会話", "vi": "Hội thoại trong project này"},
    "workspace.new_chat": {"en": "New chat in this project", "ja": "このプロジェクトで新規チャット", "vi": "Chat mới trong project này"},
    "workspace.default_new_name": {"en": "New project", "ja": "新規プロジェクト", "vi": "Project mới"},
    "workspace.collapse_projects_tooltip": {"en": "Collapse the project list", "ja": "プロジェクト一覧を折りたたむ", "vi": "Thu gọn danh sách project"},
    "workspace.expand_projects_tooltip": {"en": "Click to expand the project list", "ja": "クリックしてプロジェクト一覧を展開", "vi": "Bấm để mở rộng danh sách project"},
    "app.status.ready": {"en": "Ready.", "ja": "準備完了。", "vi": "Sẵn sàng."},
    "app.status.using_provider": {"en": "Using {label}.", "ja": "{label} を使用中。", "vi": "Đang dùng {label}."},
    "app.status.settings_saved": {"en": "Settings saved.", "ja": "設定を保存しました。", "vi": "Đã lưu cài đặt."},
    "app.credit": {"en": "Made by QuanDH14", "ja": "Made by QuanDH14", "vi": "Made by QuanDH14"},
    "app.tray.open": {"en": "Open Cowork Local", "ja": "Cowork Local を開く", "vi": "Mở Cowork Local"},
    "app.tray.quit": {"en": "Quit", "ja": "終了", "vi": "Thoát"},
    "app.tray.running_body": {
        "en": "Running in the background — tasks keep working. Right-click the tray icon to Quit.",
        "ja": "バックグラウンドで実行中です。タスクは継続します。終了するにはトレイアイコンを右クリックしてください。",
        "vi": "Đang chạy nền — tác vụ vẫn tiếp tục. Chuột phải vào biểu tượng khay để Thoát.",
    },
    "app.toast.done": {"en": "{name}: done", "ja": "{name}: 完了", "vi": "{name}: hoàn thành"},
    "app.toast.error": {"en": "{name}: error", "ja": "{name}: エラー", "vi": "{name}: lỗi"},
    "app.toast.task_done": {"en": "Task done: {title}", "ja": "タスク完了: {title}",
                             "vi": "Task hoàn thành: {title}"},
    "app.toast.task_failed": {"en": "Task failed: {title}", "ja": "タスク失敗: {title}",
                               "vi": "Task lỗi: {title}"},

    # ---- sidebar.py (History) ----------------------------------------
    "sidebar.header": {"en": "History", "ja": "履歴", "vi": "Lịch sử"},
    "sidebar.filter.all": {"en": "All", "ja": "すべて", "vi": "Tất cả"},
    "sidebar.filter.cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "sidebar.filter.code": {"en": "Code", "ja": "Code", "vi": "Code"},
    "sidebar.search_placeholder": {
        "en": "Search by title or content…", "ja": "タイトルまたは内容で検索…",
        "vi": "Tìm theo tiêu đề hoặc nội dung…"},
    "sidebar.search_tooltip": {
        "en": "Search conversation history by title or message content.",
        "ja": "会話履歴をタイトルまたはメッセージ内容で検索します。",
        "vi": "Tìm kiếm lịch sử hội thoại theo tiêu đề hoặc nội dung tin nhắn."},
    "sidebar.no_matches": {"en": "(no matches)", "ja": "（一致なし）", "vi": "(không tìm thấy)"},
    "sidebar.refresh": {"en": "Refresh", "ja": "更新", "vi": "Làm mới"},
    "sidebar.refresh_tooltip": {
        "en": "Update the list + this conversation's agent status",
        "ja": "一覧とこの会話のエージェント状態を更新",
        "vi": "Cập nhật danh sách + trạng thái agent của hội thoại đang xem",
    },
    "sidebar.empty": {"en": "(empty)", "ja": "（空）", "vi": "(trống)"},
    "sidebar.running_suffix": {"en": "   · running", "ja": "   · 実行中", "vi": "   · đang chạy"},
    "sidebar.expand_tooltip": {
        "en": "Click to expand the History panel", "ja": "クリックして履歴パネルを展開",
        "vi": "Bấm để mở lại bảng Lịch sử"},
    "sidebar.collapse_tooltip": {
        "en": "Collapse the History panel", "ja": "履歴パネルを折りたたむ",
        "vi": "Thu gọn bảng Lịch sử"},
    "sidebar.menu.pin": {"en": "Pin", "ja": "ピン留め", "vi": "Ghim"},
    "sidebar.menu.unpin": {"en": "Unpin", "ja": "ピン留め解除", "vi": "Bỏ ghim"},
    "sidebar.menu.rename": {"en": "Rename…", "ja": "名前を変更…", "vi": "Đổi tên…"},
    "sidebar.menu.delete": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "sidebar.rename.title": {"en": "Rename conversation", "ja": "会話の名前を変更", "vi": "Đổi tên hội thoại"},
    "sidebar.rename.label": {"en": "New name:", "ja": "新しい名前:", "vi": "Tên mới:"},
    "sidebar.delete.title": {"en": "Delete conversation", "ja": "会話を削除", "vi": "Xóa hội thoại"},
    "sidebar.delete.confirm": {"en": "Delete '{title}'?", "ja": "「{title}」を削除しますか？", "vi": "Xóa '{title}'?"},
    "sidebar.menu.delete_selected": {"en": "Delete {n} selected", "ja": "選択した{n}件を削除", "vi": "Xóa {n} mục đã chọn"},
    "sidebar.delete_multi.confirm": {
        "en": "Delete {n} selected conversations? This cannot be undone.",
        "ja": "選択した{n}件の会話を削除しますか？元に戻せません。",
        "vi": "Xóa {n} hội thoại đã chọn? Không thể hoàn tác."},

    # ---- widgets.py (Plan / Files sections, collapse strips) ---------
    "widgets.plan_title": {"en": "Plan", "ja": "プラン", "vi": "Plan"},
    "widgets.input_files": {"en": "Input files", "ja": "入力ファイル", "vi": "Tệp đầu vào"},
    "widgets.output_files": {"en": "Output files", "ja": "出力ファイル", "vi": "Tệp đầu ra"},

    # ---- chat_view.py --------------------------------------------------
    "chat.running": {"en": "Running", "ja": "実行中", "vi": "Đang chạy"},
    "chat.thinking": {"en": "Thinking", "ja": "思考中", "vi": "Đang nghĩ"},
    "chat.creating": {"en": "Creating", "ja": "作成中", "vi": "Đang tạo"},
    "chat.editing": {"en": "Editing", "ja": "編集中", "vi": "Đang sửa"},
    "chat.installing": {"en": "Installing", "ja": "インストール中", "vi": "Đang cài đặt"},
    "chat.reading": {"en": "Reading", "ja": "読み込み中", "vi": "Đang đọc"},
    "chat.you": {"en": "You", "ja": "あなた", "vi": "Bạn"},
    "chat.assistant": {"en": "Assistant", "ja": "アシスタント", "vi": "Assistant"},
    "chat.error": {"en": "Error", "ja": "エラー", "vi": "Lỗi"},
    "help_agent.title": {
        "en": "App Assistant", "ja": "アプリアシスタント", "vi": "Trợ lý App"},
    "help_agent.greeting": {
        "en": "Hello {name}, have a great working day! How can I help you use the app?",
        "ja": "こんにちは {name} さん、良い一日を！アプリの使い方について何かお手伝いできますか？",
        "vi": "Xin chào {name}, chúc bạn một ngày làm việc vui vẻ! Mình có thể giúp gì cho bạn khi dùng app?"},
    "help_agent.default_user": {"en": "Admin", "ja": "Admin", "vi": "Admin"},
    "help_agent.placeholder": {
        "en": "Ask how to use the app…", "ja": "アプリの使い方を質問…",
        "vi": "Hỏi cách sử dụng app…"},
    "help_agent.open_tooltip": {
        "en": "App Assistant — help using the app",
        "ja": "アプリアシスタント — アプリの使い方をサポート",
        "vi": "Trợ lý App — hỗ trợ sử dụng app"},
    "help_agent.collapse_tooltip": {
        "en": "Minimize", "ja": "最小化", "vi": "Thu nhỏ"},
    "help_agent.hide_tooltip": {
        "en": "Hide to the edge", "ja": "端に隠す", "vi": "Ẩn vào cạnh phải"},
    "help_agent.show_tooltip": {
        "en": "Show the App Assistant", "ja": "アプリアシスタントを表示",
        "vi": "Hiện App Assistant"},
    "help_agent.empty_reply": {
        "en": "(no answer)", "ja": "(回答なし)", "vi": "(không có phản hồi)"},
    "help_agent.error": {
        "en": "Sorry, I couldn't answer right now: {error}",
        "ja": "申し訳ありません、今は回答できませんでした: {error}",
        "vi": "Xin lỗi, hiện chưa thể trả lời: {error}"},
    "chat.model_switched": {
        "en": "↻ Auto-switched to {model} — re-checking the previous step, then continuing.",
        "ja": "↻ {model} に自動切り替え — 直前のステップを確認してから続行します。",
        "vi": "↻ Đã tự động chuyển sang {model} — kiểm tra lại bước trước rồi tiếp tục."},
    "chat.provider_default_short": {
        "en": "the provider's default model", "ja": "プロバイダー既定のモデル",
        "vi": "model mặc định của provider"},
    "chat.delete_link": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "chat.delete_tooltip": {
        "en": "Delete this message and its input/output files",
        "ja": "このメッセージと入出力ファイルを削除",
        "vi": "Xóa tin nhắn này và các tệp input/output của nó"},
    "chat.open_workspace": {"en": "Open workspace", "ja": "作業フォルダを開く", "vi": "Mở thư mục làm việc"},
    "chat.open_folder": {"en": "Open folder", "ja": "フォルダを開く", "vi": "Mở thư mục"},
    "chat.open_output_folder": {"en": "Open output folder", "ja": "出力フォルダを開く", "vi": "Mở thư mục output"},
    "chat.done_marker": {"en": "Done", "ja": "完了しました", "vi": "Đã hoàn thành"},
    "chat.session_folder_marker": {
        "en": "This conversation's output folder", "ja": "この会話の出力フォルダ",
        "vi": "Thư mục output của hội thoại này"},
    "chat.open_folder_short": {"en": "Open folder", "ja": "フォルダを開く", "vi": "Mở thư mục"},
    "chat.diff_before": {"en": "Before", "ja": "編集前", "vi": "Trước khi sửa"},
    "chat.diff_after": {"en": "After", "ja": "編集後", "vi": "Sau khi sửa"},
    "chat.diff_added": {"en": "Added", "ja": "追加", "vi": "Thêm mới"},
    "chat.diff_removed": {"en": "Removed", "ja": "削除", "vi": "Đã xóa"},
    "chat.attachment_warning_title": {
        "en": "Attachment", "ja": "添付ファイル", "vi": "Tệp đính kèm"},
    "chat.attachment_failed": {
        "en": "Could not read \"{name}\": {note}",
        "ja": "「{name}」を読み込めませんでした: {note}",
        "vi": "Không đọc được nội dung \"{name}\": {note}"},
    "chat.reading_progress": {
        "en": "Reading {name} — page {page}/{total}…",
        "ja": "{name} を読み込み中 — {page}/{total} ページ…",
        "vi": "Đang đọc {name} — trang {page}/{total}…"},
    "chat.workspace_files_capped": {
        "en": "Folder has more files than the per-message limit — loaded {shown}/{total} (raise it in Settings → Attachments)",
        "ja": "フォルダ内のファイル数が1メッセージあたりの上限を超えています — {shown}/{total} 件を読み込みました（ 設定 → 添付ファイルで変更可）",
        "vi": "Thư mục có nhiều file hơn giới hạn mỗi tin nhắn — đã đọc {shown}/{total} file (đổi trong Settings → Attachments)"},

    # ---- chat_panel.py (shared by Cowork & Code) ----------------------
    "chatpanel.agent_label": {"en": "Agent:", "ja": "エージェント:", "vi": "Agent:"},
    # ---- Auto Model Assessment & Routing (core/routing/) -----------------
    "routing.toggle_label": {"en": "Routing:", "ja": "ルーティング:", "vi": "Định tuyến:"},
    "routing.autorun_label": {"en": "Auto-run", "ja": "自動実行", "vi": "Tự chạy"},
    "routing.autorun_tooltip": {
        "en": "Auto-approve commands in THIS workspace (no confirm dialog).\nUnchecked: ask before each command. Each workspace keeps its own setting.",
        "ja": "このワークスペースでコマンドを自動承認（確認なし）。\nオフ: 実行前に確認。ワークスペースごとに設定を保持します。",
        "vi": "Tự động duyệt lệnh trong workspace NÀY (không hỏi xác nhận).\nBỏ chọn: hỏi trước mỗi lệnh. Mỗi workspace giữ thiết lập riêng.",
    },
    "routing.mode_off": {"en": "Off", "ja": "オフ", "vi": "Tắt"},
    "routing.mode_auto": {"en": "Auto", "ja": "自動", "vi": "Tự động"},
    "routing.mode_manual": {"en": "Manual", "ja": "手動", "vi": "Thủ công"},
    "routing.toggle_tooltip": {
        "en": "Auto model routing for this chat.\nOff: always use the selected model.\nAuto: silently switch to the best-fit model.\nManual: ask before switching.",
        "ja": "このチャットの自動モデルルーティング。\nオフ: 選択したモデルを常に使用。\n自動: 最適なモデルへ自動切替。\n手動: 切替前に確認。",
        "vi": "Tự động định tuyến model cho khung chat này.\nTắt: luôn dùng model đã chọn.\nTự động: tự chuyển sang model phù hợp nhất.\nThủ công: hỏi xác nhận trước khi chuyển.",
    },
    "routing.confirm_title": {
        "en": "Switch model?", "ja": "モデルを切り替えますか？", "vi": "Chuyển model?",
    },
    "routing.confirm_body": {
        "en": "A better-fit model was found for this {task} task:\n\n{from_model}  →  {to_model}\n(fit gain +{gain})\n\n{reason}\n\nSwitch to it for this message?",
        "ja": "この {task} タスクにより適したモデルが見つかりました:\n\n{from_model}  →  {to_model}\n(適合度 +{gain})\n\n{reason}\n\nこのメッセージで切り替えますか？",
        "vi": "Đã tìm thấy model phù hợp hơn cho tác vụ {task} này:\n\n{from_model}  →  {to_model}\n(điểm phù hợp +{gain})\n\n{reason}\n\nChuyển sang model đó cho tin nhắn này?",
    },
    "routing.confirm_yes": {"en": "Switch", "ja": "切り替える", "vi": "Chuyển"},
    "routing.confirm_no": {"en": "Keep current", "ja": "現状維持", "vi": "Giữ nguyên"},
    "routing.confirm_countdown": {
        "en": "Keep current ({secs}s)", "ja": "現状維持 ({secs}秒)", "vi": "Giữ nguyên ({secs}s)",
    },
    "routing.switched_notice": {
        "en": "↪ Auto-routed to {model} ({task}, fit +{gain})",
        "ja": "↪ {model} へ自動ルーティング ({task}, 適合度 +{gain})",
        "vi": "↪ Đã tự chuyển sang {model} ({task}, phù hợp +{gain})",
    },
    "routing.reassessing": {
        "en": "Assessing models…", "ja": "モデルを評価中…", "vi": "Đang đánh giá model…",
    },
    "routing.reassess_done": {
        "en": "Model assessment complete: {count} model(s) scored.",
        "ja": "モデル評価完了: {count} 件を採点しました。",
        "vi": "Đánh giá model xong: đã chấm {count} model.",
    },
    # ---- Routing settings group (settings_dialog.py) ---------------------
    "routing.settings_group": {
        "en": "Auto Model Routing", "ja": "自動モデルルーティング", "vi": "Tự động định tuyến Model",
    },
    "routing.settings_mode": {"en": "Default mode", "ja": "既定モード", "vi": "Chế độ mặc định"},
    "routing.settings_policy": {"en": "Policy", "ja": "ポリシー", "vi": "Chính sách"},
    "routing.policy_quality": {"en": "Quality", "ja": "品質", "vi": "Chất lượng"},
    "routing.policy_cost": {"en": "Cost", "ja": "コスト", "vi": "Chi phí"},
    "routing.policy_latency": {"en": "Latency", "ja": "レイテンシ", "vi": "Độ trễ"},
    "routing.policy_balanced": {"en": "Balanced", "ja": "バランス", "vi": "Cân bằng"},
    "routing.settings_min_gain": {
        "en": "Min score gain to switch", "ja": "切替に必要な最小スコア差", "vi": "Chênh điểm tối thiểu để chuyển",
    },
    "routing.settings_timeout": {
        "en": "Confirm timeout (sec)", "ja": "確認タイムアウト (秒)", "vi": "Thời gian chờ xác nhận (giây)",
    },
    "routing.settings_interval": {
        "en": "Reassess every (hours, 0=off)", "ja": "再評価間隔 (時間, 0=無効)", "vi": "Đánh giá lại mỗi (giờ, 0=tắt)",
    },
    "routing.settings_concurrency": {
        "en": "Max probe calls per provider", "ja": "プロバイダーごとの最大プローブ数", "vi": "Số lần probe tối đa mỗi provider",
    },
    "routing.settings_judge": {
        "en": "Judge model (blank = auto)", "ja": "ジャッジモデル (空欄=自動)", "vi": "Model chấm điểm (trống = tự động)",
    },
    "routing.settings_reassess_now": {
        "en": "Reassess now", "ja": "今すぐ再評価", "vi": "Đánh giá lại ngay",
    },
    "routing.settings_hint": {
        "en": "The app benchmarks each model and routes chats to the best-fit one. Probing spends tokens, so it runs on a schedule / when you add a model / when you click Reassess.",
        "ja": "各モデルをベンチマークし、最適なモデルへチャットを振り分けます。プローブはトークンを消費するため、スケジュール・モデル追加時・「再評価」押下時のみ実行されます。",
        "vi": "Ứng dụng benchmark từng model và định tuyến chat tới model phù hợp nhất. Probe tốn token nên chỉ chạy theo lịch / khi thêm model / khi bấm Đánh giá lại.",
    },
    "chatpanel.menu_open": {"en": "Open", "ja": "開く", "vi": "Mở"},
    "chatpanel.menu_ai_edit": {"en": "View & AI edit", "ja": "表示 & AI編集", "vi": "Xem & sửa bằng AI"},
    # ---- file_edit_dialog.py (view file + AI edit) -----------------------
    "fileedit.title": {"en": "View & edit file", "ja": "ファイル表示・編集", "vi": "Xem & sửa file"},
    "fileedit.browse_tooltip": {"en": "Open another file…", "ja": "別のファイルを開く…",
                                "vi": "Mở file khác…"},
    "fileedit.reload_tooltip": {"en": "Reload from disk", "ja": "ディスクから再読み込み",
                                "vi": "Tải lại từ đĩa"},
    "fileedit.pick_hint": {"en": "Open a file to view or edit it.",
                           "ja": "表示・編集するファイルを開いてください。",
                           "vi": "Mở một file để xem hoặc chỉnh sửa."},
    "fileedit.instruction_placeholder": {
        "en": "Tell the AI how to edit this file (e.g. 'fix typos', 'translate to English')…",
        "ja": "このファイルの編集内容をAIに指示（例:「誤字修正」「英語に翻訳」）…",
        "vi": "Nói cho AI cách sửa file này (vd: 'sửa lỗi chính tả', 'dịch sang tiếng Anh')…"},
    "fileedit.ai_btn": {"en": "AI Edit", "ja": "AI編集", "vi": "Sửa bằng AI"},
    "fileedit.save_btn": {"en": "Save", "ja": "保存", "vi": "Lưu"},
    "fileedit.close_btn": {"en": "Close", "ja": "閉じる", "vi": "Đóng"},
    "fileedit.loaded_editable": {"en": "Text file — editable.", "ja": "テキストファイル — 編集可能。",
                                 "vi": "File văn bản — có thể sửa."},
    "fileedit.loaded_readonly": {
        "en": "Binary/large document — extracted text shown, read-only (view & ask only).",
        "ja": "バイナリ/大きい文書 — 抽出テキストを表示（閲覧のみ、編集不可）。",
        "vi": "Tài liệu nhị phân/lớn — hiển thị text trích xuất, chỉ đọc (chỉ xem & hỏi)."},
    "fileedit.not_found": {"en": "File not found: {path}", "ja": "ファイルが見つかりません: {path}",
                           "vi": "Không tìm thấy file: {path}"},
    "fileedit.needs_instruction": {"en": "Enter an edit instruction first.",
                                   "ja": "先に編集指示を入力してください。",
                                   "vi": "Hãy nhập yêu cầu chỉnh sửa trước."},
    "fileedit.ai_working": {"en": "AI is editing…", "ja": "AIが編集中…", "vi": "AI đang chỉnh sửa…"},
    "fileedit.ai_done": {"en": "AI edit applied — review, then Save.",
                         "ja": "AI編集を適用 — 確認して保存してください。",
                         "vi": "Đã áp dụng chỉnh sửa của AI — xem lại rồi Lưu."},
    "fileedit.ai_empty": {"en": "The AI returned no content.", "ja": "AIが内容を返しませんでした。",
                          "vi": "AI không trả về nội dung."},
    "fileedit.ai_failed": {"en": "AI edit failed: {err}", "ja": "AI編集に失敗: {err}",
                           "vi": "Sửa bằng AI thất bại: {err}"},
    "fileedit.saved": {"en": "Saved {path} (original backed up as .bak).",
                       "ja": "{path} を保存（元は .bak にバックアップ）。",
                       "vi": "Đã lưu {path} (bản gốc sao lưu thành .bak)."},
    "chatpanel.agent_tooltip": {
        "en": "Model/agent for THIS tab — independent of the other tab",
        "ja": "このタブ専用のモデル/エージェント（他のタブとは独立）",
        "vi": "Model/agent riêng cho tab này — độc lập với tab kia"},
    "chatpanel.agent_list_error": {
        "en": "Could not load the model list: {err}", "ja": "モデル一覧を読み込めませんでした: {err}",
        "vi": "Không tải được danh sách model: {err}"},
    "chatpanel.compress_btn": {"en": "Compress", "ja": "圧縮", "vi": "Nén"},
    "chatpanel.compress_tooltip": {
        "en": "Compress the conversation: trim old history to cut tokens (avoid exceeding the context limit)",
        "ja": "会話を圧縮：古い履歴を減らしてトークンを削減（コンテキスト上限超過を回避）",
        "vi": "Nén hội thoại: bỏ bớt lịch sử cũ để giảm token (tránh lỗi vượt giới hạn context)"},
    "chatpanel.files_header": {"en": "Files", "ja": "ファイル", "vi": "Files"},
    "chatpanel.collapse_files_tooltip": {
        "en": "Collapse the Files panel", "ja": "ファイルパネルを折りたたむ", "vi": "Thu gọn bảng Files"},
    "chatpanel.expand_files_tooltip": {
        "en": "Click to expand the Files panel", "ja": "クリックしてファイルパネルを展開",
        "vi": "Bấm để mở lại bảng Files"},
    "chatpanel.compress_busy": {
        "en": "Running — stop or wait before compressing.", "ja": "実行中です。停止するか完了を待ってから圧縮してください。",
        "vi": "Đang chạy — dừng hoặc đợi xong rồi hãy nén."},
    "chatpanel.compress_short": {
        "en": "Conversation is already short — no need to compress.",
        "ja": "会話はすでに短いため圧縮の必要はありません。",
        "vi": "Hội thoại đã ngắn — không cần nén."},
    "chatpanel.compress_done": {
        "en": "Compressed: dropped {cut} old messages, kept the last {keep} turns to cut tokens.",
        "ja": "圧縮しました：古いメッセージ{cut}件を削除し、直近{keep}ターンを保持してトークンを削減。",
        "vi": "Đã nén hội thoại: bỏ {cut} tin cũ, giữ {keep} lượt gần nhất để giảm token."},
    "chatpanel.compress_reduced": {
        "en": "Compressed to {pct}% of the original ({n} old messages digested).",
        "ja": "元の {pct}% まで圧縮（古いメッセージ {n} 件を要約）。",
        "vi": "Đã nén còn {pct}% so với ban đầu ({n} tin cũ được tóm gọn)."},
    "chatpanel.compress_digest_header": {
        "en": "Compressed summary of {n} earlier messages",
        "ja": "以前のメッセージ {n} 件の要約",
        "vi": "Tóm tắt nén của {n} tin nhắn trước đó"},
    "chatpanel.delete_confirm_title": {"en": "Delete message", "ja": "メッセージを削除", "vi": "Xóa tin nhắn"},
    "chatpanel.delete_confirm_files": {
        "en": "Delete this message and its {n} input/output file(s)?\n\n{preview}",
        "ja": "このメッセージと入出力ファイル{n}件を削除しますか？\n\n{preview}",
        "vi": "Xóa tin nhắn này và {n} tệp input/output của nó?\n\n{preview}"},
    "chatpanel.delete_confirm_plain": {"en": "Delete this message?", "ja": "このメッセージを削除しますか？", "vi": "Xóa tin nhắn này?"},
    "chatpanel.delete_done": {
        "en": "Message and its files deleted.", "ja": "メッセージとファイルを削除しました。",
        "vi": "Đã xóa tin nhắn và các tệp liên quan."},
    "chatpanel.working": {"en": "{name}: working…", "ja": "{name}: 処理中…", "vi": "{name}: đang xử lý…"},
    "chatpanel.done": {"en": "{name}: done.", "ja": "{name}: 完了。", "vi": "{name}: xong."},
    "chatpanel.failed": {"en": "{name}: error.", "ja": "{name}: エラー。", "vi": "{name}: lỗi."},
    "chatpanel.stopping": {"en": "{name}: stopping…", "ja": "{name}: 停止中…", "vi": "{name}: đang dừng…"},
    "chatpanel.attach_limit": {
        "en": "Max {n} attachments — extra files were skipped.",
        "ja": "添付は最大{n}件です。超過分はスキップされました。",
        "vi": "Tối đa {n} tệp đính kèm — bỏ qua phần dư."},
    "chatpanel.attached_hint": {"en": "Attached: {names}", "ja": "添付: {names}", "vi": "Đã đính kèm: {names}"},
    "chatpanel.skills_updated": {"en": "Skills updated.", "ja": "スキルを更新しました。", "vi": "Đã cập nhật skill."},
    "chatpanel.new_files_detected": {
        "en": "New file(s) detected in output folder: {names} ({n} file(s)). They will be auto-loaded as input on the next message.",
        "ja": "出力フォルダに新しいファイルを検出しました: {names} ({n}ファイル)。次のメッセージで自動的に入力として読み込まれます。",
        "vi": "Phát hiện tệp mới trong thư mục đầu ra: {names} ({n} tệp). Chúng sẽ được tự động tải làm dữ liệu đầu vào ở tin nhắn tiếp theo.",
    },
    # ---- composer.py -----------------------------------------------
    "composer.placeholder_default": {
        "en": "Type a message…  (Enter to send, Shift+Enter for newline)",
        "ja": "メッセージを入力…（Enterで送信、Shift+Enterで改行）",
        "vi": "Nhập tin nhắn…  (Enter để gửi, Shift+Enter xuống dòng)"},
    "composer.placeholder_cowork": {
        "en": "Type a request or attach a file to process…  (Enter to send)",
        "ja": "依頼内容を入力するかファイルを添付…（Enterで送信）",
        "vi": "Nhập yêu cầu hoặc đính kèm tệp để xử lí…  (Enter để gửi)"},
    "composer.placeholder_code": {
        "en": "Assign a task to the Code agent…  (Enter to send)",
        "ja": "Code エージェントにタスクを指示…（Enterで送信）",
        "vi": "Giao việc cho Code agent…  (Enter để gửi)"},
    "composer.queue_label": {"en": "Queue ({n})", "ja": "キュー ({n})", "vi": "Hàng đợi ({n})"},
    "composer.queue_tooltip": {
        "en": "Double-click to remove a queued message", "ja": "ダブルクリックでキューから削除",
        "vi": "Nhấp đúp để xoá một tin nhắn khỏi hàng đợi"},
    "composer.attachments_label": {"en": "Attachments ({n})", "ja": "添付ファイル ({n})", "vi": "Tệp đính kèm ({n})"},
    "composer.attachments_tooltip": {
        "en": "Click on a chip to remove a file added by mistake",
        "ja": "誤って追加したファイルは で削除できます",
        "vi": "Bấm trên thẻ để gỡ tệp đính kèm nhầm"},
    "composer.remove_tooltip": {
        "en": "Remove this file (added by mistake)", "ja": "このファイルを削除（誤って追加）",
        "vi": "Gỡ tệp này (đính kèm nhầm)"},
    "composer.attach_btn_tooltip": {
        "en": "Attach images or files (you can also paste or drag them in)",
        "ja": "画像やファイルを添付（貼り付け・ドラッグも可）",
        "vi": "Đính kèm ảnh hoặc tệp (có thể dán hoặc kéo-thả vào)"},
    "composer.send": {"en": "Send", "ja": "送信", "vi": "Gửi"},
    "composer.queue_btn": {"en": "Queue", "ja": "キューに追加", "vi": "Thêm vào hàng đợi"},
    "composer.stop": {"en": "Stop", "ja": "停止", "vi": "Dừng"},
    "composer.attach_dialog_title": {"en": "Attach files / images", "ja": "ファイル/画像を添付", "vi": "Đính kèm tệp / ảnh"},
    "composer.attach_dialog_filter": {
        "en": "Files (*.*);;Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
        "ja": "ファイル (*.*);;画像 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
        "vi": "Tệp (*.*);;Ảnh (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"},
    "composer.no_skills": {"en": "   (no skills yet)", "ja": "   （スキルはまだありません）", "vi": "   (chưa có skill nào)"},
    "composer.no_agents": {"en": "   (no agents found)", "ja": "   （エージェントが見つかりません）", "vi": "   (không tìm thấy agent)"},
    "composer.manage_skills": {"en": "Manage skills…", "ja": "スキルを管理…", "vi": "Quản lý skill…"},

    # ---- schedule_task_tab.py / task_editor_dialog.py -------------------
    "schedtask.title": {"en": "Schedule Task", "ja": "Schedule Task", "vi": "Schedule Task"},
    "schedtask.view.kanban": {"en": "Kanban", "ja": "Kanban", "vi": "Kanban"},
    "schedtask.view.calendar": {"en": "Calendar", "ja": "カレンダー", "vi": "Lịch"},
    "schedtask.no_title": {"en": "(untitled)", "ja": "（無題）", "vi": "(chưa có tên)"},
    "schedtask.cal_today": {"en": "Today", "ja": "今日", "vi": "Hôm nay"},
    "schedtask.cal_prev": {"en": "Previous", "ja": "前へ", "vi": "Trước"},
    "schedtask.cal_next": {"en": "Next", "ja": "次へ", "vi": "Sau"},
    "schedtask.cal_gran.week": {"en": "Week", "ja": "週", "vi": "Tuần"},
    "schedtask.cal_gran.month": {"en": "Month", "ja": "月", "vi": "Tháng"},
    "schedtask.cal_gran.year": {"en": "Year", "ja": "年", "vi": "Năm"},
    "schedtask.cal_weekday.mon": {"en": "Mon", "ja": "月", "vi": "T2"},
    "schedtask.cal_weekday.tue": {"en": "Tue", "ja": "火", "vi": "T3"},
    "schedtask.cal_weekday.wed": {"en": "Wed", "ja": "水", "vi": "T4"},
    "schedtask.cal_weekday.thu": {"en": "Thu", "ja": "木", "vi": "T5"},
    "schedtask.cal_weekday.fri": {"en": "Fri", "ja": "金", "vi": "T6"},
    "schedtask.cal_weekday.sat": {"en": "Sat", "ja": "土", "vi": "T7"},
    "schedtask.cal_weekday.sun": {"en": "Sun", "ja": "日", "vi": "CN"},
    "schedtask.cal_month_count": {"en": "{month} — {n} task(s)", "ja": "{month} — {n} 件",
                                  "vi": "{month} — {n} task"},
    "schedtask.search_ph": {"en": "Search tasks…", "ja": "タスクを検索…", "vi": "Tìm task…"},
    "schedtask.filter_all": {"en": "All types", "ja": "すべての種類", "vi": "Mọi loại"},
    "schedtask.add_btn": {"en": "Add Task", "ja": "タスク追加", "vi": "Thêm Task"},
    "schedtask.ai_btn": {"en": "AI Create Task", "ja": "AIでタスク作成", "vi": "AI tạo Task"},
    "schedtask.ai_tooltip": {
        "en": "Describe what you want in natural language — AI proposes tasks/schedule/chain, you confirm before anything is created.",
        "ja": "自然文で説明すると、AIがタスク・スケジュール・チェーンを提案します。確認後に作成されます。",
        "vi": "Mô tả bằng ngôn ngữ tự nhiên — AI đề xuất task/lịch/chuỗi, bạn xác nhận rồi mới tạo."},
    "schedtask.no_tasks": {"en": "No tasks", "ja": "タスクなし", "vi": "No tasks"},
    "schedtask.no_schedule": {"en": "No schedule", "ja": "スケジュールなし", "vi": "Chưa đặt lịch"},
    "schedtask.last_success": {"en": "Last: Success", "ja": "前回: 成功", "vi": "Lần cuối: Thành công"},
    "schedtask.last_failed": {"en": "Last: Failed", "ja": "前回: 失敗", "vi": "Lần cuối: Lỗi"},
    "schedtask.last_never": {"en": "Last: not run", "ja": "前回: 未実行", "vi": "Lần cuối: chưa chạy"},
    "schedtask.status.backlog": {"en": "Backlog", "ja": "Backlog", "vi": "Backlog"},
    "schedtask.status.scheduled": {"en": "Scheduled", "ja": "Scheduled", "vi": "Scheduled"},
    "schedtask.status.running": {"en": "Running", "ja": "Running", "vi": "Running"},
    "schedtask.status.waiting_input": {"en": "Waiting Input", "ja": "Waiting Input", "vi": "Waiting Input"},
    "schedtask.status.done": {"en": "Done", "ja": "Done", "vi": "Done"},
    "schedtask.status.failed": {"en": "Failed", "ja": "Failed", "vi": "Failed"},
    "schedtask.status.paused": {"en": "Paused", "ja": "Paused", "vi": "Paused"},
    "schedtask.type.cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "schedtask.type.co4e_code": {"en": "Code", "ja": "Code", "vi": "Code"},
    "schedtask.type.flow": {"en": "Flow", "ja": "Flow", "vi": "Flow"},
    "schedtask.type.script": {"en": "Script", "ja": "Script", "vi": "Script"},
    "schedtask.type.manual": {"en": "Manual", "ja": "Manual", "vi": "Manual"},
    "schedtask.priority.low": {"en": "Low", "ja": "低", "vi": "Thấp"},
    "schedtask.priority.medium": {"en": "Medium", "ja": "中", "vi": "Trung bình"},
    "schedtask.priority.high": {"en": "High", "ja": "高", "vi": "Cao"},
    "schedtask.priority.critical": {"en": "Critical", "ja": "最重要", "vi": "Khẩn cấp"},
    "schedtask.menu_run": {"en": "Run now", "ja": "今すぐ実行", "vi": "Chạy ngay"},
    "schedtask.menu_edit": {"en": "Edit task", "ja": "タスクを編集", "vi": "Sửa task"},
    "schedtask.menu_duplicate": {"en": "Duplicate task", "ja": "タスクを複製", "vi": "Nhân bản task"},
    "schedtask.menu_pause": {"en": "Pause", "ja": "一時停止", "vi": "Tạm dừng"},
    "schedtask.menu_resume": {"en": "Resume", "ja": "再開", "vi": "Tiếp tục"},
    "schedtask.menu_logs": {"en": "View logs", "ja": "ログを表示", "vi": "Xem log"},
    "schedtask.menu_history": {"en": "Run history…", "ja": "実行履歴…", "vi": "Lịch sử chạy…"},
    "schedtask.hist_hint": {
        "en": "Double-click a row to open that run's artifact folder.",
        "ja": "行をダブルクリックすると、その実行のフォルダを開きます。",
        "vi": "Double-click một dòng để mở thư mục artifact của lần chạy đó."},
    "schedtask.hist_col_time": {"en": "Finished at", "ja": "完了時刻", "vi": "Hoàn thành lúc"},
    "schedtask.hist_col_status": {"en": "Status", "ja": "状態", "vi": "Trạng thái"},
    "schedtask.hist_col_run": {"en": "Run ID", "ja": "実行ID", "vi": "Run ID"},
    "schedtask.hist_col_error": {"en": "Error", "ja": "エラー", "vi": "Lỗi"},
    "schedtask.menu_create_next": {
        "en": "Create next task from output", "ja": "出力から次タスクを作成",
        "vi": "Tạo task tiếp theo từ output"},
    "schedtask.menu_delete": {"en": "Delete task", "ja": "タスクを削除", "vi": "Xóa task"},
    "schedtask.delete_confirm": {"en": "Delete '{title}'?", "ja": "「{title}」を削除しますか？", "vi": "Xóa '{title}'?"},
    "schedtask.menu_delete_selected": {"en": "Delete {n} selected", "ja": "選択した{n}件を削除", "vi": "Xóa {n} task đã chọn"},
    "schedtask.delete_multi_confirm": {
        "en": "Delete {n} selected tasks? This cannot be undone.",
        "ja": "選択した{n}件のタスクを削除しますか？元に戻せません。",
        "vi": "Xóa {n} task đã chọn? Không thể hoàn tác."},
    "schedtask.msg_created": {"en": "Task created.", "ja": "タスクを作成しました。", "vi": "Đã tạo task."},
    "schedtask.msg_running": {"en": "Running: {title}", "ja": "実行中: {title}", "vi": "Đang chạy: {title}"},
    "schedtask.msg_manual_norun": {
        "en": "Manual tasks are for tracking only — they don't execute.",
        "ja": "Manualタスクは管理用のため実行されません。",
        "vi": "Task Manual chỉ để quản lý — không tự chạy."},
    "schedtask.msg_no_scheduler": {"en": "Scheduler not available.", "ja": "スケジューラーが利用できません。", "vi": "Scheduler chưa sẵn sàng."},
    "schedtask.msg_ai_created": {"en": "Created {n} task(s) from AI plan.", "ja": "AI提案から{n}件のタスクを作成しました。", "vi": "Đã tạo {n} task từ đề xuất AI."},
    "schedtask.no_runs_yet": {"en": "This task has not run yet.", "ja": "このタスクはまだ実行されていません。", "vi": "Task này chưa chạy lần nào."},
    "schedtask.next_of": {"en": "Next: {title}", "ja": "次: {title}", "vi": "Tiếp theo: {title}"},
    # editor
    "schedtask.editor_title_new": {"en": "Add Task", "ja": "タスク追加", "vi": "Thêm Task"},
    "schedtask.editor_title_edit": {"en": "Edit Task", "ja": "タスク編集", "vi": "Sửa Task"},
    "schedtask.f_title": {"en": "Title", "ja": "タイトル", "vi": "Tiêu đề"},
    "schedtask.f_desc": {"en": "Description", "ja": "説明", "vi": "Mô tả"},
    "schedtask.f_type": {"en": "Task type", "ja": "タスク種別", "vi": "Loại task"},
    "schedtask.f_workspace": {"en": "Workspace", "ja": "ワークスペース", "vi": "Workspace"},
    "schedtask.no_workspace": {"en": "— No workspace —", "ja": "— ワークスペースなし —", "vi": "— Không có workspace —"},
    "schedtask.f_agent": {"en": "Agent", "ja": "エージェント", "vi": "Agent"},
    "schedtask.no_agent": {"en": "— No agent preset —", "ja": "— エージェントなし —", "vi": "— Không dùng agent —"},
    "schedtask.f_provider": {"en": "Provider", "ja": "プロバイダー", "vi": "Provider"},
    "schedtask.provider_default": {
        "en": "— Default (Settings) —", "ja": "— 既定（設定）—", "vi": "— Mặc định (Settings) —"},
    "schedtask.f_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "schedtask.model_placeholder": {
        "en": "Default model (leave blank to use Settings)",
        "ja": "既定のモデル（空欄で設定を使用）",
        "vi": "Model mặc định (để trống dùng Settings)"},
    "schedtask.load_models_tooltip": {
        "en": "Fetch this provider's available models",
        "ja": "このプロバイダーの利用可能なモデルを取得",
        "vi": "Tải danh sách model của provider này"},
    "schedtask.load_models_empty": {
        "en": "No models could be loaded. Check the provider/API key in Settings.",
        "ja": "モデルを取得できませんでした。設定のプロバイダー/APIキーを確認してください。",
        "vi": "Không tải được model nào. Kiểm tra provider/API key trong Settings."},
    "schedtask.f_skill": {"en": "Skill", "ja": "スキル", "vi": "Skill"},
    "schedtask.no_skill": {"en": "— No skill —", "ja": "— スキルなし —", "vi": "— Không dùng skill —"},
    "schedtask.hint_provider": {
        "en": "Which AI provider runs this task. Leave as Default to use the machine's Settings provider.",
        "ja": "このタスクを実行するAIプロバイダー。既定のままにすると設定のプロバイダーを使用します。",
        "vi": "Provider AI chạy task này. Để Mặc định để dùng provider trong Settings."},
    "schedtask.hint_model": {
        "en": "Model to run this task. Leave blank to use the provider's Settings model; click the button to load the real list.",
        "ja": "このタスクを実行するモデル。空欄で設定のモデルを使用。ボタンで実際の一覧を取得します。",
        "vi": "Model chạy task này. Để trống dùng model trong Settings; bấm nút để tải danh sách thực."},
    "schedtask.hint_skill": {
        "en": "Apply a saved skill's instructions to this task's run (its guidance is prepended to the prompt).",
        "ja": "保存済みスキルの指示をこのタスクの実行に適用します（プロンプトの先頭に追加されます）。",
        "vi": "Áp dụng hướng dẫn của một skill đã lưu vào lần chạy task này (được thêm vào đầu prompt)."},
    "schedtask.hint_workspace": {
        "en": "The project/workspace this task's agent runs in — its sandbox folder and shared instructions apply.",
        "ja": "このタスクのエージェントが実行されるプロジェクト/ワークスペース。そのサンドボックスフォルダと共有指示が適用されます。",
        "vi": "Project/workspace mà agent của task này sẽ chạy trong đó — áp dụng sandbox và hướng dẫn chung của project."},
    "schedtask.f_priority": {"en": "Priority", "ja": "優先度", "vi": "Độ ưu tiên"},
    "schedtask.f_status": {"en": "Status", "ja": "ステータス", "vi": "Trạng thái"},
    "schedtask.f_script": {"en": "Script command", "ja": "スクリプトコマンド", "vi": "Lệnh script"},
    "schedtask.script_placeholder": {
        "en": "(script tasks only) e.g. python report.py", "ja": "（Scriptタスクのみ）例: python report.py",
        "vi": "(chỉ task Script) vd: python report.py"},
    "schedtask.g_schedule": {"en": "Schedule Setup", "ja": "スケジュール設定", "vi": "Thiết lập lịch chạy"},
    "schedtask.sched_enable": {"en": "Enable schedule", "ja": "スケジュールを有効化", "vi": "Bật lịch chạy"},
    "schedtask.f_run_at": {"en": "Run at", "ja": "実行日時", "vi": "Chạy lúc"},
    "schedtask.f_repeat": {"en": "Repeat", "ja": "繰り返し", "vi": "Lặp lại"},
    "schedtask.repeat.none": {"en": "None (one-time)", "ja": "なし（1回のみ）", "vi": "Không (chạy 1 lần)"},
    "schedtask.repeat.daily": {"en": "Daily", "ja": "毎日", "vi": "Hằng ngày"},
    "schedtask.repeat.weekly": {"en": "Weekly", "ja": "毎週", "vi": "Hằng tuần"},
    "schedtask.repeat.monthly": {"en": "Monthly", "ja": "毎月", "vi": "Hằng tháng"},
    "schedtask.repeat.cron": {"en": "Cron expression", "ja": "Cron式", "vi": "Cron expression"},
    "schedtask.f_task_mode": {"en": "Task type", "ja": "タスク種別", "vi": "Loại task"},
    # Run kind: an AI agent vs a saved Co4E flow + multi-format import
    "schedtask.f_run_kind": {"en": "Run", "ja": "実行対象", "vi": "Chạy"},
    "schedtask.kind_agent": {"en": "AI agent (Cowork)", "ja": "AIエージェント（Cowork）",
                             "vi": "AI agent (Cowork)"},
    "schedtask.kind_flow": {"en": "Co4E flow", "ja": "Co4E フロー", "vi": "Co4E flow"},
    "schedtask.hint_run_kind": {
        "en": "AI agent = run one Cowork agent with the chosen model. Co4E flow = run a whole "
              "saved node-graph flow, step by step, in the sandbox.",
        "ja": "AIエージェント＝選択モデルで Cowork エージェントを1つ実行。Co4E フロー＝保存済みの"
              "ノードグラフ全体をサンドボックスで順に実行。",
        "vi": "AI agent = chạy một agent Cowork với model đã chọn. Co4E flow = chạy cả một flow "
              "node-graph đã lưu, tuần tự, trong sandbox."},
    "schedtask.f_flow": {"en": "Co4E flow", "ja": "Co4E フロー", "vi": "Co4E flow"},
    "schedtask.hint_flow": {
        "en": "Which saved Co4E flow this task runs (built-in or your own).",
        "ja": "このタスクが実行する保存済み Co4E フロー（組込み／自作）。",
        "vi": "Flow Co4E đã lưu mà task này sẽ chạy (có sẵn hoặc của bạn)."},
    "schedtask.flow_required": {
        "en": "Pick a Co4E flow to run (or switch Run to AI agent).",
        "ja": "実行する Co4E フローを選んでください（または実行対象を AI エージェントに）。",
        "vi": "Hãy chọn một flow Co4E để chạy (hoặc đổi Chạy sang AI agent)."},
    "schedtask.hint_task_mode": {
        "en": "Normal = runs once (or manually). Automation = a cronjob that repeats on a schedule "
              "(daily/weekly/monthly/cron). Switching to Automation reveals the recurrence options.",
        "ja": "通常＝1回（または手動）実行。自動化＝スケジュールで繰り返すCronジョブ（毎日/毎週/毎月/Cron）。"
              "自動化に切り替えると繰り返し設定が表示されます。",
        "vi": "Thông thường = chạy một lần (hoặc thủ công). Tự động = cronjob lặp theo lịch "
              "(ngày/tuần/tháng/cron). Chuyển sang Tự động sẽ hiện các tùy chọn lặp lại."},
    "schedtask.mode_normal": {
        "en": "Normal (one-time / manual)", "ja": "通常（1回 / 手動）",
        "vi": "Thông thường (một lần / thủ công)"},
    "schedtask.mode_automation": {
        "en": "Automation (cron / recurring)", "ja": "自動化（Cron / 繰り返し）",
        "vi": "Tự động (cronjob / lặp lại)"},
    "schedtask.f_cron": {"en": "Cron", "ja": "Cron", "vi": "Cron"},
    "schedtask.cron_sample_pick": {"en": "Sample ▾", "ja": "サンプル ▾", "vi": "Mẫu ▾"},
    "schedtask.cron_sample_tooltip": {
        "en": "Pick a ready-made schedule — it fills the cron box with correct syntax.",
        "ja": "定番スケジュールを選ぶと、正しい書式でCron欄に入力されます。",
        "vi": "Chọn một lịch mẫu — sẽ điền đúng cú pháp vào ô cron."},
    "schedtask.cron_s_weekday9": {"en": "Weekdays 9:00", "ja": "平日 9:00", "vi": "Ngày làm việc 9:00"},
    "schedtask.cron_s_daily8": {"en": "Every day 8:00", "ja": "毎日 8:00", "vi": "Mỗi ngày 8:00"},
    "schedtask.cron_s_weekly_mon": {"en": "Every Monday 9:00", "ja": "毎週月曜 9:00", "vi": "Thứ 2 hằng tuần 9:00"},
    "schedtask.cron_s_monthly1": {"en": "1st of month 9:00", "ja": "毎月1日 9:00", "vi": "Ngày 1 hằng tháng 9:00"},
    "schedtask.cron_s_every30m": {"en": "Every 30 minutes", "ja": "30分ごと", "vi": "Mỗi 30 phút"},
    "schedtask.cron_s_every2h": {"en": "Every 2 hours", "ja": "2時間ごと", "vi": "Mỗi 2 giờ"},
    "schedtask.cron_placeholder": {
        "en": "(repeat = Cron) e.g. 0 9 * * 1-5  — min hour day month weekday",
        "ja": "（繰り返し=Cron）例: 0 9 * * 1-5 — 分 時 日 月 曜日",
        "vi": "(khi lặp = Cron) vd: 0 9 * * 1-5 — phút giờ ngày tháng thứ"},
    "schedtask.cron_hint": {
        "en": "Only when Repeat = Cron. Fields: minute hour day-of-month month day-of-week "
              "(e.g. '0 9 * * 1-5' = 9:00 every weekday). Otherwise the run-time above is the "
              "daily/weekly/monthly notification time.",
        "ja": "繰り返し=Cronの場合のみ。書式: 分 時 日 月 曜日（例 '0 9 * * 1-5' = 平日9:00）。"
              "それ以外は上の実行時刻が毎日/毎週/毎月の通知時刻になります。",
        "vi": "Chỉ khi Lặp = Cron. Cú pháp: phút giờ ngày tháng thứ (vd '0 9 * * 1-5' = 9:00 các "
              "ngày trong tuần). Nếu không, giờ chạy ở trên là giờ thông báo hàng ngày/tuần/tháng."},
    "schedtask.cron_invalid": {
        "en": "Invalid cron expression: {err}", "ja": "Cron式が不正です: {err}",
        "vi": "Cron expression không hợp lệ: {err}"},
    "schedtask.cron_never_fires": {
        "en": "This cron expression never fires (within 2 years).",
        "ja": "このCron式は（2年以内に）一度も実行されません。",
        "vi": "Cron expression này không bao giờ chạy (trong vòng 2 năm)."},
    "schedtask.workdays_only": {"en": "Working days only (skip Sat/Sun)", "ja": "平日のみ（土日をスキップ）", "vi": "Chỉ ngày làm việc (bỏ T7/CN)"},
    "schedtask.skip_holidays": {
        "en": "Skip public holidays", "ja": "祝日をスキップ", "vi": "Bỏ qua ngày nghỉ lễ"},
    "schedtask.holiday_country": {"en": "Country:", "ja": "国:", "vi": "Quốc gia:"},
    "schedtask.f_notify": {"en": "Reminder", "ja": "リマインダー", "vi": "Nhắc nhở"},
    "schedtask.f_notify_email": {"en": "Send to", "ja": "送信先", "vi": "Gửi tới"},
    "schedtask.notify.none": {"en": "— No reminder —", "ja": "— リマインダーなし —", "vi": "— Không nhắc —"},
    "schedtask.notify.teams": {"en": "Teams (webhook)", "ja": "Teams（Webhook）", "vi": "Teams (webhook)"},
    "schedtask.notify.outlook": {
        "en": "Email via Outlook (this PC)", "ja": "Outlookでメール（このPC）",
        "vi": "Email qua Outlook (máy này)"},
    "schedtask.notify_email_placeholder": {
        "en": "recipient@example.com (comma-separated)",
        "ja": "recipient@example.com（カンマ区切り）",
        "vi": "nguoinhan@example.com (cách nhau dấu phẩy)"},
    "schedtask.notify_hint": {
        "en": "When the scheduled/cron task finishes, send a reminder. Teams uses the webhook "
              "from Settings; Outlook sends from your signed-in Outlook desktop app — no login needed.",
        "ja": "スケジュール/Cronタスク完了時にリマインダーを送信。Teamsは設定のWebhookを使用、"
              "Outlookはサインイン済みのOutlookデスクトップから送信（ログイン不要）。",
        "vi": "Khi task theo lịch/cron chạy xong sẽ gửi nhắc. Teams dùng webhook trong Settings; "
              "Outlook gửi từ ứng dụng Outlook đã đăng nhập trên máy — không cần đăng nhập lại."},
    "schedtask.notify_need_email": {
        "en": "Enter a recipient address for the Outlook reminder.",
        "ja": "Outlookリマインダーの送信先アドレスを入力してください。",
        "vi": "Hãy nhập địa chỉ người nhận cho nhắc nhở qua Outlook."},
    "schedtask.notify_need_webhook": {
        "en": "Teams reminder needs a webhook URL — set it in Settings → Parameter first.",
        "ja": "TeamsリマインダーにはWebhook URLが必要です。先に設定→パラメータで設定してください。",
        "vi": "Nhắc qua Teams cần webhook URL — hãy đặt trong Settings → Parameter trước."},
    "schedtask.tz_local_note": {
        "en": "Times use this machine's local timezone.", "ja": "時刻はこのPCのローカルタイムゾーンです。",
        "vi": "Giờ dùng múi giờ local của máy này."},
    "schedtask.g_flow": {"en": "Flow Setup", "ja": "フロー設定", "vi": "Thiết lập Flow"},
    "schedtask.flow_hint": {
        "en": "(Flow tasks only) Steps run in order; each step's output feeds the next step's input.",
        "ja": "（Flowタスクのみ）ステップは順番に実行され、前ステップの出力が次の入力になります。",
        "vi": "(Chỉ task Flow) Các bước chạy tuần tự; output bước trước nối vào input bước sau."},
    "schedtask.flow_template": {"en": "Code template:", "ja": "Codeテンプレート:", "vi": "Template Code:"},
    "schedtask.import_flow_btn": {"en": "Import steps", "ja": "ステップ取込", "vi": "Nhập các bước"},
    "schedtask.flow_template_empty": {
        "en": "The selected template has no steps.", "ja": "選択したテンプレートにステップがありません。",
        "vi": "Template đã chọn không có bước nào."},
    "schedtask.step_name_ph": {"en": "Step name", "ja": "ステップ名", "vi": "Tên bước"},
    "schedtask.step_prompt_ph": {"en": "Prompt / command", "ja": "プロンプト/コマンド", "vi": "Prompt / lệnh"},
    "schedtask.stepexec.cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "schedtask.stepexec.co4e": {"en": "Code", "ja": "Code", "vi": "Code"},
    "schedtask.stepexec.script": {"en": "Script", "ja": "Script", "vi": "Script"},
    "schedtask.stepexec.manual": {"en": "Manual", "ja": "Manual", "vi": "Manual"},
    "schedtask.del_step_tooltip": {"en": "Delete the selected step", "ja": "選択したステップを削除",
                                    "vi": "Xóa bước đang chọn"},
    "schedtask.guide_tooltip": {
        "en": "Open the Schedule Task user guide", "ja": "Schedule Task の使い方ガイドを開く",
        "vi": "Mở hướng dẫn sử dụng Schedule Task"},
    "schedtask.guide_missing": {
        "en": "Guide file not found (docs/schedule_task_user_guide.md).",
        "ja": "ガイドファイルが見つかりません (docs/schedule_task_user_guide.md)。",
        "vi": "Không tìm thấy file hướng dẫn (docs/schedule_task_user_guide.md)."},
    "schedtask.msg_set_schedule": {
        "en": "Set a run time so this task can actually run on schedule.",
        "ja": "実行時刻を設定するとスケジュール実行されます。",
        "vi": "Hãy đặt giờ chạy để task này thực sự chạy theo lịch."},
    # ---- hint tooltips (hover help) --------------------------------------
    "schedtask.add_tooltip": {
        "en": "Create a new task with full options (schedule, input, dependencies…).",
        "ja": "新しいタスクを作成（スケジュール・入力・依存など全設定）。",
        "vi": "Tạo task mới với đầy đủ tuỳ chọn (lịch, input, phụ thuộc…)."},
    "schedtask.search_tooltip": {
        "en": "Filter cards by title/description.", "ja": "タイトル/説明でカードを絞り込み。",
        "vi": "Lọc card theo tiêu đề/mô tả."},
    "schedtask.filter_tooltip": {
        "en": "Show only one task type.", "ja": "1つのタスク種別のみ表示。",
        "vi": "Chỉ hiện một loại task."},
    "schedtask.col_tip.backlog": {
        "en": "New tasks with no schedule yet. Drag a card here to shelve it.",
        "ja": "未スケジュールの新規タスク。", "vi": "Task mới, chưa đặt lịch. Kéo card vào đây để cất lại."},
    "schedtask.col_tip.scheduled": {
        "en": "On the calendar — runs automatically at its time. Drop a card here to schedule it.",
        "ja": "スケジュール済み — 時刻になると自動実行。", "vi": "Đã lên lịch — tự chạy khi đến giờ. Thả card vào đây để đặt lịch."},
    "schedtask.col_tip.running": {
        "en": "Currently executing. Drop a card here to RUN it immediately.",
        "ja": "実行中。ここにドロップすると即実行します。", "vi": "Đang chạy. Thả card vào đây để CHẠY NGAY."},
    "schedtask.col_tip.waiting_input": {
        "en": "Waiting: needs your Run-now approval, or its prerequisite tasks aren't Done yet.",
        "ja": "待機中: 手動承認待ち、または前提タスクが未完了。",
        "vi": "Đang chờ: cần bạn bấm Chạy ngay (phê duyệt), hoặc các task phụ thuộc chưa Done."},
    "schedtask.col_tip.done": {
        "en": "Finished successfully. Drop a card here to mark it done by hand.",
        "ja": "完了。ここにドロップすると手動で完了扱いにします。",
        "vi": "Đã xong. Thả card vào đây để tự đánh dấu hoàn thành."},
    "schedtask.col_tip.failed": {
        "en": "Last run errored — right-click → Run history to see why.",
        "ja": "前回失敗 — 右クリック→実行履歴で原因を確認。",
        "vi": "Lần chạy cuối bị lỗi — chuột phải → Lịch sử chạy để xem lý do."},
    "schedtask.col_tip.paused": {
        "en": "Paused: never auto-runs and is skipped by chains until resumed.",
        "ja": "一時停止中: 再開まで自動実行されず、チェーンでもスキップされます。",
        "vi": "Tạm dừng: không tự chạy và bị chuỗi bỏ qua cho tới khi tiếp tục."},
    "schedtask.hint_type": {
        "en": "Cowork = documents/answers · Code = coding agent · Script = shell command · Flow = multi-step · Manual = tracking only.",
        "ja": "Cowork=文書/回答 · Code=コーディング · Script=コマンド · Flow=複数ステップ · Manual=管理のみ。",
        "vi": "Cowork = tài liệu/trả lời · Code = agent code · Script = lệnh shell · Flow = nhiều bước · Manual = chỉ quản lý."},
    "schedtask.hint_status": {
        "en": "Current Kanban lane. Usually managed automatically by the scheduler.",
        "ja": "現在のKanbanレーン。通常はスケジューラーが自動管理。",
        "vi": "Cột Kanban hiện tại. Thường được scheduler tự quản lý."},
    "schedtask.hint_script": {
        "en": "Shell command to run (Script tasks). Runs in the task's artifact folder with a timeout.",
        "ja": "実行するシェルコマンド（Scriptタスク）。", "vi": "Lệnh shell sẽ chạy (task Script), trong thư mục artifact riêng, có timeout."},
    "schedtask.hint_sched_enable": {
        "en": "Off = the task never runs by itself.", "ja": "OFF = 自動実行されません。",
        "vi": "Tắt = task không bao giờ tự chạy."},
    "schedtask.hint_run_at": {
        "en": "First/next run time (this machine's local time).",
        "ja": "初回/次回の実行時刻（ローカル時刻）。", "vi": "Giờ chạy đầu/kế tiếp (giờ local của máy)."},
    "schedtask.hint_repeat": {
        "en": "After a successful run, the schedule rolls to the next occurrence automatically.",
        "ja": "成功後、次回分へ自動的に繰り越します。",
        "vi": "Sau khi chạy thành công, lịch tự dời sang kỳ kế tiếp."},
    "schedtask.hint_cron": {
        "en": "5 fields: minute hour day month weekday. E.g. '0 9 * * 1-5' = 9:00 on weekdays.",
        "ja": "5項目: 分 時 日 月 曜日。例 '0 9 * * 1-5' = 平日9時。",
        "vi": "5 trường: phút giờ ngày tháng thứ. VD '0 9 * * 1-5' = 9h các ngày thường."},
    "schedtask.hint_workdays": {
        "en": "Runs landing on Sat/Sun are pushed to the next working day.",
        "ja": "土日に当たる回は翌営業日に繰り越し。", "vi": "Lịch rơi vào T7/CN sẽ dời sang ngày làm việc kế."},
    "schedtask.hint_holidays": {
        "en": "Runs landing on a public holiday of the chosen country are pushed to the next allowed day.",
        "ja": "選択した国の祝日に当たる回は翌営業日に繰り越し。",
        "vi": "Lịch rơi vào ngày lễ của quốc gia đã chọn sẽ tự dời sang ngày hợp lệ kế."},
    "schedtask.hint_country": {
        "en": "ISO country code for the holiday calendar (VN, JP, US… — type any code).",
        "ja": "祝日カレンダーの国コード（VN, JP, US…）。", "vi": "Mã quốc gia cho lịch nghỉ lễ (VN, JP, US… — gõ được mã bất kỳ)."},
    "schedtask.hint_flow_template": {
        "en": "Import the stages of a saved Flow template as steps here.",
        "ja": "保存済みFlowテンプレートをステップとして取り込み。",
        "vi": "Nhập các stage của Flow template đã lưu thành các bước ở đây."},
    "schedtask.hint_input_mode": {
        "en": "What the agent receives besides the description: nothing, typed text, file contents, or the output of earlier tasks.",
        "ja": "説明に加えてエージェントへ渡す入力。", "vi": "Agent nhận gì ngoài mô tả: trống, văn bản gõ tay, nội dung tệp, hoặc output các task trước."},
    "schedtask.hint_prev_task": {
        "en": "Single explicit source task for 'previous task output' (leave (none) to use all waited-for tasks).",
        "ja": "「前タスクの出力」の明示的なソース。", "vi": "Task nguồn cụ thể cho 'output task trước' (để (không) sẽ dùng tất cả task đang chờ)."},
    "schedtask.hint_output_mode": {
        "en": "Expected output format — informational for now, files always land in the artifact folder.",
        "ja": "想定する出力形式（参考情報）。", "vi": "Định dạng output mong muốn — hiện mang tính thông tin, file luôn nằm trong thư mục artifact."},
    "schedtask.hint_next_task": {
        "en": "Task to trigger after this one finishes (chain).",
        "ja": "このタスク完了後に起動するタスク（チェーン）。", "vi": "Task được kích hoạt sau khi task này xong (chuỗi)."},
    "schedtask.hint_run_next": {
        "en": "When the next task fires: on success / always / only after you confirm.",
        "ja": "次タスクの起動条件: 成功時/常に/手動確認後。", "vi": "Khi nào task sau chạy: khi thành công / luôn / chờ bạn xác nhận."},
    "schedtask.hint_pass_output": {
        "en": "This task's output.md becomes the next task's input automatically.",
        "ja": "このタスクのoutput.mdを次タスクの入力に自動投入。",
        "vi": "output.md của task này tự thành input của task sau."},
    "schedtask.hint_depends": {
        "en": "Fan-in: this task waits until ALL ticked tasks are Done, then runs automatically with their outputs available.",
        "ja": "ファンイン: チェックした全タスクがDoneになるまで待機し、自動実行。",
        "vi": "Fan-in: task này đợi TẤT CẢ task được tick Done rồi mới tự chạy, kèm output của chúng."},
    "schedtask.hint_retry": {
        "en": "Auto-retry this many times when a run fails.", "ja": "失敗時の自動リトライ回数。",
        "vi": "Tự thử lại bấy nhiêu lần khi chạy lỗi."},
    "schedtask.hint_timeout": {
        "en": "Hard limit per run (Script tasks).", "ja": "1回あたりの上限時間（Script）。",
        "vi": "Giới hạn thời gian mỗi lần chạy (task Script)."},
    "schedtask.hint_approval": {
        "en": "Safety: the scheduler will NEVER auto-run this — it parks in Waiting Input until you right-click → Run now.",
        "ja": "安全: 自動実行されず、Run nowまで待機します。",
        "vi": "An toàn: scheduler KHÔNG BAO GIỜ tự chạy task này — nó nằm ở Waiting Input tới khi bạn chuột phải → Chạy ngay."},
    "schedtask.g_input": {"en": "Input", "ja": "入力", "vi": "Input"},
    "schedtask.f_input_mode": {"en": "Input mode", "ja": "入力モード", "vi": "Chế độ input"},
    "schedtask.inmode.empty": {"en": "Empty (default)", "ja": "空（既定）", "vi": "Trống (mặc định)"},
    "schedtask.inmode.manual": {"en": "Manual text", "ja": "手入力テキスト", "vi": "Văn bản nhập tay"},
    "schedtask.inmode.file": {"en": "File(s)", "ja": "ファイル", "vi": "Tệp"},
    "schedtask.inmode.previous_task_output": {
        "en": "Previous task output", "ja": "前タスクの出力", "vi": "Output của task trước"},
    "schedtask.f_manual_text": {"en": "Prompt", "ja": "プロンプト", "vi": "Prompt"},
    "schedtask.gen_input_tooltip": {
        "en": "AI-draft the prompt from the title/description", "ja": "タイトル/説明からプロンプトをAI生成",
        "vi": "AI soạn prompt từ tiêu đề/mô tả"},
    "schedtask.f_files": {"en": "Attach files", "ja": "添付ファイル", "vi": "Đính kèm tệp"},
    "schedtask.f_links": {"en": "Attach links", "ja": "添付リンク", "vi": "Đính kèm link"},
    "schedtask.files_placeholder": {
        "en": "Local file paths, separated by ;", "ja": "ローカルファイルパス（;区切り）",
        "vi": "Đường dẫn tệp local, cách nhau bằng ;"},
    "schedtask.links_placeholder": {
        "en": "https://…  URLs separated by ;", "ja": "https://…  URL（;区切り）",
        "vi": "https://…  các link, cách nhau bằng ;"},
    "schedtask.pick_files": {"en": "Browse…", "ja": "参照…", "vi": "Chọn tệp…"},
    "schedtask.add_link_title": {"en": "Add link", "ja": "リンクを追加", "vi": "Thêm link"},
    "schedtask.add_link_label": {"en": "URL:", "ja": "URL:", "vi": "URL:"},
    "schedtask.hint_files": {
        "en": "Attached files are always read and given to the agent as context, regardless of Input mode.",
        "ja": "添付ファイルはInputモードに関係なく常にエージェントへ渡されます。",
        "vi": "Tệp đính kèm luôn được đọc và đưa vào ngữ cảnh cho agent, bất kể chế độ Input."},
    "schedtask.hint_links": {
        "en": "Each URL is fetched (best-effort) and its text content given to the agent as context.",
        "ja": "各URLを取得し（ベストエフォート）、テキストをコンテキストとして渡します。",
        "vi": "Mỗi link được tải nội dung (khi có thể) và đưa vào ngữ cảnh cho agent."},
    "schedtask.f_prev_task": {"en": "Previous task", "ja": "前タスク", "vi": "Task trước"},
    "schedtask.g_output": {"en": "Output", "ja": "出力", "vi": "Output"},
    "schedtask.f_output_mode": {"en": "Output mode", "ja": "出力モード", "vi": "Chế độ output"},
    "schedtask.g_dependency": {"en": "Dependency / Next task", "ja": "依存 / 次タスク", "vi": "Phụ thuộc / Task tiếp theo"},
    "schedtask.f_next_task": {"en": "Next task", "ja": "次タスク", "vi": "Task tiếp theo"},
    "schedtask.f_run_next": {"en": "Run next task", "ja": "次タスクの実行", "vi": "Chạy task tiếp theo"},
    "schedtask.runnext.none": {"en": "Don't run next task", "ja": "実行しない", "vi": "Không chạy task sau"},
    "schedtask.runnext.run_after_success": {
        "en": "Run after success", "ja": "成功後に実行", "vi": "Chạy khi task này thành công"},
    "schedtask.runnext.run_always": {"en": "Always run", "ja": "常に実行", "vi": "Luôn chạy (kể cả lỗi)"},
    "schedtask.runnext.run_after_manual_confirm": {
        "en": "Wait for my confirmation", "ja": "手動確認後に実行", "vi": "Chờ tôi xác nhận rồi chạy"},
    "schedtask.pass_output": {
        "en": "Use this task's output as next task's input",
        "ja": "このタスクの出力を次タスクの入力にする",
        "vi": "Dùng output task này làm input task sau"},
    "schedtask.next_paused_warn": {
        "en": "The selected next task is paused — it will be skipped when this task finishes.",
        "ja": "選択した次タスクは一時停止中のため、完了時にスキップされます。",
        "vi": "Task tiếp theo đang tạm dừng — sẽ bị bỏ qua khi task này chạy xong."},
    "schedtask.none": {"en": "(none)", "ja": "（なし）", "vi": "(không)"},
    "schedtask.g_execution": {"en": "Execution", "ja": "実行設定", "vi": "Thực thi"},
    "schedtask.f_retry": {"en": "Max retry", "ja": "最大リトライ", "vi": "Số lần thử lại"},
    "schedtask.f_timeout": {"en": "Timeout", "ja": "タイムアウト", "vi": "Thời gian tối đa"},
    "schedtask.requires_approval": {
        "en": "Requires approval (scheduler will NOT auto-run; waits for Run now)",
        "ja": "承認必須（自動実行されず、手動のRun nowを待ちます）",
        "vi": "Cần phê duyệt (không tự chạy theo lịch; chờ bấm Chạy ngay)"},
    "schedtask.notify_ok": {"en": "Notify Teams on complete", "ja": "完了時にTeams通知", "vi": "Báo Teams khi xong"},
    "schedtask.notify_err": {"en": "Notify Teams on error", "ja": "エラー時にTeams通知", "vi": "Báo Teams khi lỗi"},
    "schedtask.title_required": {"en": "Please enter a title.", "ja": "タイトルを入力してください。", "vi": "Vui lòng nhập tiêu đề."},
    # AI create dialog
    "schedtask.ai_desc_label": {
        "en": "Describe what you want to automate:", "ja": "自動化したい内容を記述:",
        "vi": "Mô tả việc bạn muốn tự động hoá:"},
    "schedtask.ai_desc_ph": {
        "en": "e.g. Every Monday 9:00, use Code to read new CAE data and build a markdown report, then have Cowork draft a team email from it.",
        "ja": "例: 毎週月曜9時、CodeでCAEデータを読み込みレポート作成、その後Coworkでメール下書きを作成。",
        "vi": "vd: Mỗi thứ 2 lúc 9h, dùng Code đọc dữ liệu CAE mới tạo báo cáo markdown, sau đó Cowork soạn email draft gửi team."},
    "schedtask.ai_generate": {"en": "Generate plan", "ja": "プランを生成", "vi": "Tạo kế hoạch"},
    "schedtask.ai_generating": {"en": "Generating…", "ja": "生成中…", "vi": "Đang tạo…"},
    "schedtask.ai_preview_label": {
        "en": "Preview (nothing is created until you confirm):",
        "ja": "プレビュー（確認するまで作成されません）:",
        "vi": "Xem trước (chưa tạo gì cho tới khi bạn xác nhận):"},
    "schedtask.ai_confirm": {"en": "Create tasks", "ja": "タスクを作成", "vi": "Tạo các task"},
    "schedtask.tab_ai": {"en": "AI gen task", "ja": "AIタスク生成", "vi": "AI gen task"},
    "schedtask.tab_import": {"en": "Import", "ja": "インポート", "vi": "Import"},
    "schedtask.export_template_btn": {
        "en": "Create Excel template…", "ja": "Excelテンプレートを作成…",
        "vi": "Tạo template Excel…"},
    "schedtask.import_pick_btn": {"en": "Choose file…", "ja": "ファイルを選択…", "vi": "Chọn file…"},
    "schedtask.drop_hint": {
        "en": "…or drag & drop the filled .xlsx here",
        "ja": "…または記入済みの .xlsx をここにドラッグ＆ドロップ",
        "vi": "…hoặc kéo-thả file .xlsx đã điền vào đây"},
    "schedtask.f_depends_on": {
        "en": "Wait for tasks (all must be Done)", "ja": "待機するタスク（全てDone必須）",
        "vi": "Chờ các task (tất cả phải Done)"},
    "schedtask.gen_desc_tooltip": {
        "en": "Generate the Prompt from this description (the title is not used)",
        "ja": "この説明からプロンプトを生成（タイトルは使用しません）",
        "vi": "Sinh Prompt từ mô tả này (không dùng tiêu đề)"},
    "schedtask.gen_needs_description": {
        "en": "Enter a description first — the Prompt is generated from it.",
        "ja": "先に説明を入力してください。プロンプトは説明から生成されます。",
        "vi": "Hãy nhập mô tả trước — Prompt được sinh ra từ mô tả."},
    # ---- dashboard_tab.py ------------------------------------------------
    "dashboard.title": {"en": "Dashboard — token usage & cost", "ja": "Dashboard — トークン使用量とコスト",
                         "vi": "Dashboard — token & chi phí"},
    "dashboard.period.today": {"en": "Today", "ja": "今日", "vi": "Hôm nay"},
    "dashboard.period.week": {"en": "Last 7 days", "ja": "過去7日", "vi": "7 ngày qua"},
    "dashboard.period.month": {"en": "Last 30 days", "ja": "過去30日", "vi": "30 ngày qua"},
    "dashboard.period.all": {"en": "All time", "ja": "全期間", "vi": "Toàn bộ"},
    "dashboard.source_all": {"en": "All tasks/sessions", "ja": "全タスク/セッション", "vi": "Mọi task/phiên"},
    "dashboard.refresh_tooltip": {"en": "Refresh now", "ja": "今すぐ更新", "vi": "Làm mới ngay"},
    "dashboard.card_total": {"en": "Total tokens", "ja": "合計トークン", "vi": "Tổng token"},
    "dashboard.card_in": {"en": "Input", "ja": "入力", "vi": "Input"},
    "dashboard.card_out": {"en": "Output", "ja": "出力", "vi": "Output"},
    "dashboard.card_cache": {"en": "Cache", "ja": "キャッシュ", "vi": "Cache"},
    "dashboard.card_cost": {"en": "Total cost", "ja": "合計コスト", "vi": "Tổng chi phí"},
    "dashboard.card_turns": {"en": "{n} turns", "ja": "{n} ターン", "vi": "{n} lượt"},
    "dashboard.prices_label": {
        "en": "Unit price (USD / 1M tokens):", "ja": "単価 (USD / 100万トークン):",
        "vi": "Đơn giá (USD / 1 triệu token):"},
    "dashboard.price_in": {"en": "In", "ja": "入力", "vi": "In"},
    "dashboard.price_out": {"en": "Out", "ja": "出力", "vi": "Out"},
    "dashboard.price_cache": {"en": "Cache", "ja": "キャッシュ", "vi": "Cache"},
    "dashboard.habits_title": {
        "en": "Usage habits overview", "ja": "利用傾向の概要", "vi": "Tổng quan thói quen sử dụng"},
    "dashboard.chart_title": {"en": "Tokens / cost over time", "ja": "トークン／コスト推移",
                              "vi": "Token / chi phí theo thời gian"},
    "dashboard.strategy_btn": {"en": "Apply saving strategy", "ja": "節約戦略を適用",
                               "vi": "Áp dụng chiến lược tiết kiệm"},
    "dashboard.strategy_tooltip": {
        "en": "Apply the AI's cost-saving strategy: auto-compress earlier + digest context before each turn.",
        "ja": "AIの節約戦略を適用：早めに自動圧縮＋各ターン前にコンテキストを要約。",
        "vi": "Áp dụng chiến lược tiết kiệm của AI: tự động nén sớm hơn + tóm gọn ngữ cảnh trước mỗi lượt."},
    "dashboard.strategy_title": {"en": "Apply saving strategy", "ja": "節約戦略の適用",
                                 "vi": "Áp dụng chiến lược tiết kiệm"},
    "dashboard.strategy_confirm": {
        "en": "Turn on auto-compress (earlier, at 60%) and compress context before each turn to cut tokens?",
        "ja": "自動圧縮（60%で早めに）とターン前のコンテキスト圧縮を有効にしてトークンを削減しますか？",
        "vi": "Bật tự động nén (sớm hơn, ở 60%) và nén ngữ cảnh trước mỗi lượt để giảm token?"},
    "dashboard.strategy_applied": {
        "en": "Saving strategy applied: auto-compress on, compress-before-send on.",
        "ja": "節約戦略を適用：自動圧縮ON、送信前圧縮ON。",
        "vi": "Đã áp dụng: bật tự động nén và nén trước khi gửi."},
    "dashboard.gran_day": {"en": "By day", "ja": "日別", "vi": "Theo ngày"},
    "dashboard.gran_week": {"en": "By week", "ja": "週別", "vi": "Theo tuần"},
    "dashboard.gran_month": {"en": "By month", "ja": "月別", "vi": "Theo tháng"},
    "dashboard.gran_year": {"en": "By year", "ja": "年別", "vi": "Theo năm"},
    "dashboard.ref_last_week": {"en": "Last week", "ja": "先週", "vi": "Tuần trước"},
    "dashboard.ref_last_month": {"en": "Last month", "ja": "先月", "vi": "Tháng trước"},
    "dashboard.ref_last_year": {"en": "Last year", "ja": "昨年", "vi": "Năm trước"},
    "usage.budget_title": {"en": "Budget", "ja": "予算", "vi": "Budget"},
    "usage.budget_no_budget": {"en": "No budget set", "ja": "予算未設定", "vi": "Chưa đặt Budget"},
    "usage.budget_used_pct": {"en": "{pct}% used", "ja": "{pct}% 使用済み", "vi": "Đã dùng {pct}%"},
    "usage.budget_over_warning": {"en": "⚠ Over 85% of budget used",
                                  "ja": "⚠ 予算の85%以上を使用",
                                  "vi": "⚠ Đã dùng quá 85% Budget"},
    "usage.budget_apply_tooltip": {"en": "Set this as the budget (starts a fresh remaining-balance window)",
                                   "ja": "この金額を予算として設定（残高の計算を今から開始）",
                                   "vi": "Đặt số này làm Budget (tính số dư mới từ bây giờ)"},
    "usage.budget_spin_tooltip": {"en": "Enter the budget amount directly, then click ✓",
                                  "ja": "予算額を直接入力して ✓ をクリック",
                                  "vi": "Nhập Budget trực tiếp rồi bấm ✓"},
    "dashboard.chart_prev": {"en": "Previous period", "ja": "前の期間", "vi": "Kỳ trước"},
    "dashboard.chart_next": {"en": "Next period", "ja": "次の期間", "vi": "Kỳ sau"},
    "dashboard.metric_cost": {"en": "Cost", "ja": "コスト", "vi": "Chi phí"},
    "dashboard.metric_tokens": {"en": "Tokens", "ja": "トークン", "vi": "Token"},
    "dashboard.h_top": {"en": "Top token consumers (task/session)", "ja": "トークン消費上位（タスク/セッション）",
                         "vi": "Tiêu tốn token nhiều nhất (task/phiên)"},
    "dashboard.h_by_source": {"en": "By area", "ja": "領域別", "vi": "Theo khu vực"},
    "dashboard.h_avg": {"en": "Average per prompt", "ja": "1プロンプト平均", "vi": "Trung bình mỗi prompt"},
    "dashboard.h_busiest_day": {"en": "Busiest day", "ja": "最も使った日", "vi": "Ngày dùng nhiều nhất"},
    "dashboard.h_busiest_hour": {"en": "Busiest hour", "ja": "最も使う時間帯", "vi": "Khung giờ hay dùng"},
    "dashboard.no_data": {
        "en": "No usage recorded in this period yet — run a chat or a task first.",
        "ja": "この期間の使用記録はまだありません。チャットやタスクを実行してください。",
        "vi": "Chưa có dữ liệu sử dụng trong giai đoạn này — hãy chạy chat hoặc task trước."},
    "dashboard.estimated_note": {
        "en": "~{pct}% of turns are estimated (~4 chars/token) — the gateway didn't report exact usage.",
        "ja": "約{pct}%のターンは推定値（約4文字/トークン）です。",
        "vi": "~{pct}% lượt là ước tính (~4 ký tự/token) — gateway không trả về usage chính xác."},
    "dashboard.ai_analyze_btn": {"en": "AI analyze", "ja": "AI分析", "vi": "AI phân tích"},
    "dashboard.ai_analyzing": {"en": "Analyzing…", "ja": "分析中…", "vi": "Đang phân tích…"},
    "dashboard.ai_analyze_tooltip": {
        "en": "AI reviews the aggregated numbers (never your prompt contents) and suggests how to prompt better and spend fewer tokens.",
        "ja": "集計値のみをAIがレビューし（プロンプト内容は送信しません）、トークン削減のコツを提案します。",
        "vi": "AI xem các con số tổng hợp (không gửi nội dung prompt) và gợi ý cách viết prompt tốt hơn, tốn ít token hơn."},
    "dashboard.ai_advice_title": {
        "en": "AI recommendations", "ja": "AIの提案", "vi": "Khuyến nghị từ AI"},
    "dashboard.period_tooltip": {
        "en": "Time range for all numbers on this page.", "ja": "このページ全体の集計期間。",
        "vi": "Khoảng thời gian tính mọi con số trên trang này."},
    "dashboard.source_tooltip": {
        "en": "Filter by one task/session, or all.", "ja": "タスク/セッション単位で絞り込み。",
        "vi": "Lọc theo 1 task/phiên, hoặc tất cả."},
    "dashboard.currency_tooltip": {
        "en": "Display currency (rates: fixed USD→VND/JPY, editable in config).",
        "ja": "表示通貨（USD→VND/JPYの固定レート、configで変更可）。",
        "vi": "Tiền tệ hiển thị (tỉ giá USD→VND/JPY cố định, sửa được trong config)."},
    "dashboard.price_in_tooltip": {
        "en": "USD per 1M input tokens (your gateway's price).",
        "ja": "入力100万トークンあたりのUSD単価。", "vi": "USD cho 1 triệu token input (giá của gateway bạn dùng)."},
    "dashboard.price_out_tooltip": {
        "en": "USD per 1M output tokens.", "ja": "出力100万トークンあたりのUSD単価。",
        "vi": "USD cho 1 triệu token output."},
    "dashboard.price_cache_tooltip": {
        "en": "USD per 1M cached tokens.", "ja": "キャッシュ100万トークンあたりのUSD単価。",
        "vi": "USD cho 1 triệu token cache."},
    # ---- cowork_tab.py -------------------------------------------------
    "cowork.title": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "cowork.skills_btn": {"en": "Skills", "ja": "スキル", "vi": "Skills"},
    "cowork.skills_tooltip": {
        "en": "Add / manage skills the agent follows (or type /skill).",
        "ja": "エージェントが従うスキルを追加/管理（/skill と入力も可）。",
        "vi": "Thêm/quản lý skill mà agent tuân theo (hoặc gõ /skill)."},
    "cowork.new_chat": {"en": "New chat", "ja": "新しいチャット", "vi": "Cuộc trò chuyện mới"},
    "cowork.assistant_title": {"en": "Internal Agent", "ja": "内部エージェント", "vi": "Internal Agent"},
    "cowork.project_label": {"en": "{name}", "ja": "{name}", "vi": "{name}"},
    "cowork.project_tooltip": {
        "en": "This thread belongs to project “{name}” — its shared instructions and workspace apply. Manage projects in the Workspace screen.",
        "ja": "このスレッドはプロジェクト「{name}」に属します — 共有指示とワークスペースが適用されます。プロジェクトはワークスペース画面で管理できます。",
        "vi": "Thread này thuộc project “{name}” — instructions chung và workspace của project được áp dụng. Quản lý project trong màn hình Workspace.",
    },
    "cowork.pick_folder_btn": {"en": "Local folder…",
                                "ja": "ローカルフォルダ…",
                                "vi": "Thư mục Local…"},
    "cowork.pick_folder_tooltip": {
        "en": "Save Cowork's output directly into a folder you choose, instead of "
              "auto-creating a new session folder under Output.",
        "ja": "Output 配下に新しいセッションフォルダを自動作成する代わりに、選んだフォルダに直接保存します。",
        "vi": "Lưu output của Cowork trực tiếp vào thư mục bạn chọn, thay vì tự tạo "
              "folder phiên mới trong Output."},
    "cowork.pick_folder_title": {"en": "Choose the Cowork output folder",
                                  "ja": "Cowork の出力フォルダを選択",
                                  "vi": "Chọn thư mục output cho Cowork"},

    # ---- code_tab.py -----------------------------------------------
    "code.title": {"en": "Code", "ja": "Code", "vi": "Code"},
    "code.files_header": {"en": "Files", "ja": "ファイル", "vi": "Files"},
    "code.collapse_file_panel": {
        "en": "Collapse the file panel", "ja": "ファイルパネルを折りたたむ", "vi": "Thu gọn bảng cây thư mục"},
    "code.expand_file_panel": {
        "en": "Click to expand the file panel", "ja": "クリックしてファイルパネルを展開",
        "vi": "Bấm để mở lại bảng cây thư mục"},
    "code.local_btn": {"en": "Local…", "ja": "ローカル…", "vi": "Local…"},
    "code.onedrive_btn": {"en": "OneDrive", "ja": "OneDrive", "vi": "OneDrive"},
    "code.onedrive_badge": {"en": "OneDrive", "ja": "OneDrive", "vi": "OneDrive"},
    "code.auto_run": {"en": "Auto-run", "ja": "自動実行", "vi": "Tự động"},
    "code.auto_run_tooltip": {
        "en": "On: agent writes files / runs commands automatically. Off: ask before each action.",
        "ja": "オン：エージェントが自動でファイル書き込み/コマンド実行。オフ：毎回確認します。",
        "vi": "Bật: agent tự ghi file/chạy lệnh. Tắt: hỏi xác nhận trước mỗi thao tác."},
    "code.skills_tooltip": {
        "en": "Add / set skills for the agent to follow (or type /skill).",
        "ja": "エージェントが従うスキルを追加/設定（/skill と入力も可）。",
        "vi": "Thêm/đặt skill mà agent tuân theo (hoặc gõ /skill)."},
    "code.flow_chk": {"en": "Flow", "ja": "フロー", "vi": "Flow"},
    "code.flow_chk_tooltip": {
        "en": "Enable the predefined Req→Demo flow feature (off by default).",
        "ja": "定義済みの Req→Demo フロー機能を有効化（初期値はオフ）。",
        "vi": "Bật tính năng Flow Req→Demo dựng sẵn (mặc định tắt)."},
    "code.flow_btn": {"en": "Flow Management", "ja": "Flow Management", "vi": "Flow Management"},
    "code.flow_btn_tooltip": {
        "en": "Build and run a multi-stage flow from requirement to demo.",
        "ja": "要件からデモまでの多段フローを作成・実行します。",
        "vi": "Xây dựng và chạy quy trình nhiều bước từ yêu cầu đến bản demo."},
    "code.new_session": {"en": "New session", "ja": "新しいセッション", "vi": "Phiên mới"},
    "code.cli_tooltip": {
        "en": "Open a terminal (CLI) at the current working folder",
        "ja": "現在の作業フォルダでターミナル(CLI)を開く",
        "vi": "Mở CLI (terminal) tại thư mục làm việc hiện tại"},
    "code.cli_not_found": {
        "en": "No terminal application was found on this system.",
        "ja": "このシステムにはターミナルアプリが見つかりませんでした。",
        "vi": "Không tìm thấy ứng dụng terminal nào trên máy này."},
    "code.skills_btn_count": {"en": "Skills ({n})", "ja": "スキル ({n})", "vi": "Skills ({n})"},
    "code.assistant_title": {"en": "Code agent", "ja": "Code エージェント", "vi": "Code agent"},
    "code.plan": {"en": "Plan", "ja": "プラン", "vi": "Plan"},
    "code.act": {"en": "Act", "ja": "実行", "vi": "Act"},
    "code.mode_toggle_tooltip": {
        "en": "Plan = analyze only (no file writes). Act = execute. Auto-switches to Act on gencode.",
        "ja": "Plan＝分析のみ（書き込みなし）。Act＝実行。コード生成指示で自動的に Act に切替。",
        "vi": "Plan = chỉ phân tích (không ghi file). Act = thực thi. Tự chuyển sang Act khi phát hiện yêu cầu sinh code."},
    "code.act_status": {"en": "Code: Act mode (executes).", "ja": "Code: Act モード（実行）。", "vi": "Code: chế độ Act (thực thi)."},
    "code.plan_status": {"en": "Code: Plan mode (analyze only).", "ja": "Code: Plan モード（分析のみ）。", "vi": "Code: chế độ Plan (chỉ phân tích)."},
    "code.cloud_sync_suffix": {"en": "  —  files sync to cloud", "ja": "  —  クラウドに同期", "vi": "  —  file sẽ đồng bộ lên cloud"},
    "code.mode_auto_status": {"en": "Code: Auto-run mode.", "ja": "Code: 自動実行モード。", "vi": "Code: chế độ Tự động."},
    "code.mode_confirm_status": {"en": "Code: Confirm mode.", "ja": "Code: 確認モード。", "vi": "Code: chế độ Xác nhận."},
    "code.pick_local_title": {"en": "Choose working folder (Local)", "ja": "作業フォルダを選択（ローカル）", "vi": "Chọn thư mục làm việc (Local)"},
    "code.pick_onedrive_title": {"en": "Choose a folder in OneDrive", "ja": "OneDrive 内のフォルダを選択", "vi": "Chọn thư mục trong OneDrive"},
    "code.onedrive_choose_folder": {
        "en": "Choose a folder in OneDrive…", "ja": "OneDrive 内のフォルダを選択…", "vi": "Chọn thư mục trong OneDrive…"},
    "code.no_onedrive": {"en": "No OneDrive detected", "ja": "OneDrive が見つかりません", "vi": "Không phát hiện OneDrive"},
    "code.flow_running": {
        "en": "Running flow '{name}' (Act) — {n} stages.",
        "ja": "フロー「{name}」を実行中（Act）— {n} ステージ。",
        "vi": "Đang chạy flow '{name}' (Act) — {n} bước."},

    # ---- settings_dialog.py --------------------------------------------
    "settings.title": {"en": "Settings", "ja": "設定", "vi": "Cài đặt"},
    "settings.active_provider": {"en": "Active provider", "ja": "使用中のプロバイダー", "vi": "Nhà cung cấp đang dùng"},
    "settings.theme": {"en": "Theme", "ja": "テーマ", "vi": "Giao diện"},
    "settings.theme_dark": {"en": "Dark", "ja": "ダーク", "vi": "Tối"},
    "settings.theme_light": {"en": "Light", "ja": "ライト", "vi": "Sáng"},
    "settings.theme_system": {"en": "Auto (System)", "ja": "自動（システム）", "vi": "Tự động (theo hệ thống)"},
    "settings.language": {"en": "Language", "ja": "言語", "vi": "Ngôn ngữ"},
    "settings.tray_keep": {
        "en": "Keep running in the system tray when the window is closed",
        "ja": "ウィンドウを閉じてもシステムトレイで実行を継続",
        "vi": "Giữ chạy nền trong khay hệ thống khi đóng cửa sổ"},
    "settings.tray_notify": {
        "en": "Show a tray notification when a task finishes or fails",
        "ja": "タスク完了/失敗時にトレイ通知を表示",
        "vi": "Hiện thông báo khay hệ thống khi tác vụ xong hoặc lỗi"},
    "settings.group.openai": {"en": "OpenAI-compatible (Internal Gateway)", "ja": "OpenAI 互換（社内ゲートウェイ）", "vi": "OpenAI-compatible (Gateway nội bộ)"},
    "settings.group.anthropic": {"en": "Anthropic Claude", "ja": "Anthropic Claude", "vi": "Anthropic Claude"},
    "settings.group.provider": {"en": "AI Provider", "ja": "AI プロバイダー", "vi": "Nhà cung cấp AI"},
    "settings.group.parameter": {"en": "Parameter", "ja": "Parameter", "vi": "Parameter"},
    "settings.param_section_pricing": {
        "en": "Model pricing", "ja": "モデル価格", "vi": "Bảng giá model"},
    "settings.pricing_url_label": {
        "en": "Pricing reference link", "ja": "価格表の参考リンク", "vi": "Link bảng giá tham khảo"},
    "settings.pricing_url_placeholder": {
        "en": "https://… (the provider's public price list)",
        "ja": "https://…（プロバイダーの公開価格表）",
        "vi": "https://… (trang bảng giá công khai của provider)"},
    "settings.pricing_url_tooltip": {
        "en": "Shown as a reference link beside the Monitoring pricing table. Prices themselves are entered by hand in that table.",
        "ja": "監視画面の価格表の横に参考リンクとして表示されます。価格自体は表に手入力します。",
        "vi": "Hiển thị làm link tham khảo cạnh bảng giá trong Monitoring. Giá vẫn do Admin nhập tay vào bảng."},
    "settings.group.accounts": {
        "en": "Shared accounts folder", "ja": "共有アカウントフォルダー", "vi": "Thư mục tài khoản dùng chung"},
    "settings.accounts_dir_label": {"en": "Folder", "ja": "フォルダー", "vi": "Thư mục"},
    "settings.accounts_dir_placeholder": {
        "en": "OneDrive/network folder holding the shared accounts & groups",
        "ja": "アカウント/グループを保存する OneDrive・共有フォルダー",
        "vi": "Thư mục OneDrive/mạng chứa danh sách tài khoản & nhóm dùng chung"},
    "settings.accounts_dir_hint": {
        "en": "Where accounts, groups and shared telemetry live. Every machine must point at the SAME folder.",
        "ja": "アカウント・グループ・共有テレメトリの保存先。全マシンで同じフォルダーを指定してください。",
        "vi": "Nơi lưu tài khoản, nhóm và telemetry dùng chung. Mọi máy phải trỏ về CÙNG một thư mục."},
    "settings.accounts_dir_admin_only": {
        "en": "Only an Admin can change this folder.",
        "ja": "このフォルダーを変更できるのは管理者のみです。",
        "vi": "Chỉ Admin mới thay đổi được thư mục này."},
    "settings.group.monitoring_visibility": {
        "en": "Monitoring tab visibility (Sub-admin)", "ja": "モニタリングタブの表示（サブ管理者）",
        "vi": "Hiển thị tab Monitoring (Sub-admin)"},
    "settings.mv_security_events": {"en": "Security Events", "ja": "セキュリティイベント",
                                    "vi": "Security Events"},
    "settings.mv_mcp_history": {"en": "MCP Call History", "ja": "MCP 呼び出し履歴", "vi": "MCP Call History"},
    "settings.mv_action_logs": {"en": "Action Logs", "ja": "アクションログ", "vi": "Action Logs"},
    "settings.mv_agent_status": {"en": "Agent Status", "ja": "エージェント状態", "vi": "Trạng thái Agent"},
    "settings.mv_hint": {
        "en": "Admin always sees every Monitoring tab. Turn one off here to hide it from Sub-admin too (it stays available to Admin).",
        "ja": "管理者は常にすべてのタブを見られます。ここでオフにすると、そのタブはサブ管理者からも隠されます（管理者には影響しません）。",
        "vi": "Admin luôn thấy mọi tab Monitoring. Tắt một mục ở đây sẽ ẩn tab đó với Sub-admin (Admin vẫn thấy như thường)."},
    "settings.sec_unlock_user_placeholder": {
        "en": "Admin account", "ja": "管理者アカウント", "vi": "Tài khoản admin"},
    "settings.sec_unlock_code_placeholder": {
        "en": "Access code", "ja": "アクセスコード", "vi": "Mã truy cập"},
    "settings.sec_unlock_btn": {"en": "Unlock", "ja": "ロック解除", "vi": "Mở khóa"},
    "settings.sec_locked_hint": {
        "en": "Locked — enter an Admin account + access code to change these settings.",
        "ja": "ロック中 — 変更するには管理者アカウントとアクセスコードを入力してください。",
        "vi": "Đang khóa — nhập tài khoản Admin + mã truy cập để thay đổi các thiết lập này."},
    "settings.sec_unlocked_hint": {
        "en": "Unlocked — changes will be saved; the group locks again after Save.",
        "ja": "ロック解除中 — 保存後に再びロックされます。",
        "vi": "Đã mở khóa — thay đổi sẽ được lưu; nhóm sẽ tự khóa lại sau khi Save."},
    "settings.sec_unlock_failed": {
        "en": "Not an Admin account (or wrong code / accounts folder unreachable).",
        "ja": "管理者アカウントではありません（またはコード誤り・フォルダー未接続）。",
        "vi": "Không phải tài khoản Admin (hoặc sai mã / không truy cập được thư mục tài khoản)."},
    "settings.sec_no_lock_hint": {
        "en": "No shared accounts folder configured yet — the group is editable without an admin unlock.",
        "ja": "共有アカウントフォルダー未設定のため、ロックなしで編集できます。",
        "vi": "Chưa cấu hình thư mục tài khoản dùng chung — nhóm này đang chỉnh sửa được mà không cần mở khóa."},
    "settings.base_url": {"en": "Base URL", "ja": "ベース URL", "vi": "Base URL"},
    "settings.api_key": {"en": "API Key", "ja": "API キー", "vi": "API Key"},
    "settings.model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "settings.load": {"en": "Load", "ja": "読み込み", "vi": "Tải"},
    "settings.load_tooltip": {
        "en": "Fetch the available models/agents from this provider",
        "ja": "このプロバイダーから利用可能なモデル/エージェントを取得",
        "vi": "Lấy danh sách model/agent khả dụng từ nhà cung cấp này"},
    "settings.group.teams": {"en": "Microsoft Teams", "ja": "Microsoft Teams", "vi": "Microsoft Teams"},
    "settings.teams_webhook": {"en": "Webhook URL", "ja": "Webhook URL", "vi": "Webhook URL"},
    "settings.teams_webhook_placeholder": {
        "en": "https://…  (Workflows or Incoming Webhook URL)",
        "ja": "https://…（Workflows または Incoming Webhook の URL）",
        "vi": "https://…  (URL của Workflows hoặc Incoming Webhook)"},
    "settings.teams_test": {"en": "Test", "ja": "テスト", "vi": "Kiểm tra"},
    "settings.teams_notify": {
        "en": "Auto-send to Teams when a task completes", "ja": "タスク完了時に Teams へ自動送信",
        "vi": "Tự động gửi sang Teams khi tác vụ hoàn thành"},
    "settings.teams_hint": {
        "en": ("Get a webhook: Teams channel → ⋯ → Connectors → Incoming Webhook, "
               "OR Power Automate → 'When a HTTP request is received' → 'Post message in a chat or channel'. "
               "URL must contain logic.azure.com or webhook.office.com."),
        "ja": ("Webhook の取得: Teams チャンネル → ⋯ → コネクタ → Incoming Webhook、"
               "または Power Automate → 'HTTP要求の受信時' → 'チャットまたはチャネルにメッセージを投稿'。"
               "URL には logic.azure.com か webhook.office.com を含める必要があります。"),
        "vi": ("Lấy webhook: kênh Teams → ⋯ → Connectors → Incoming Webhook, "
               "HOẶC Power Automate → 'When a HTTP request is received' → 'Post message in a chat or channel'. "
               "URL phải chứa logic.azure.com hoặc webhook.office.com.")},
    "settings.group.ms365": {
        "en": "Microsoft 365 connections", "ja": "Microsoft 365 連携",
        "vi": "Kết nối Microsoft 365"},
    "settings.ms365_unlock_code": {"en": "Unlock code", "ja": "解除コード", "vi": "Mã mở khóa"},
    "settings.ms365_unlock_placeholder": {
        "en": "Enter the unlock code", "ja": "解除コードを入力",
        "vi": "Nhập mã để mở khóa"},
    "settings.ms365_unlock_btn": {"en": "Unlock", "ja": "解除", "vi": "Mở khóa"},
    "settings.ms365_locked_hint": {
        "en": "Locked — enter the unlock code above to edit this section.",
        "ja": "ロック中 — このセクションを編集するには上の解除コードを入力してください。",
        "vi": "Đang khóa — nhập mã ở trên để chỉnh sửa mục này."},
    "settings.ms365_unlocked_hint": {
        "en": "Unlocked — remember to click Save; this section re-locks automatically afterward.",
        "ja": "解除しました — 保存を忘れずに。保存後は自動的に再ロックされます。",
        "vi": "Đã mở khóa — nhớ bấm Save; mục này sẽ tự khóa lại ngay sau đó."},
    "settings.ms365_wrong_code": {
        "en": "Wrong code.", "ja": "コードが違います。", "vi": "Mã không đúng."},
    "settings.ms365_connector.outlook": {"en": "Outlook", "ja": "Outlook", "vi": "Outlook"},
    "settings.ms365_connector.teams": {"en": "Teams", "ja": "Teams", "vi": "Teams"},
    "settings.ms365_connector.onedrive": {"en": "OneDrive", "ja": "OneDrive", "vi": "OneDrive"},
    "settings.ms365_connector.sharepoint": {"en": "SharePoint", "ja": "SharePoint", "vi": "SharePoint"},
    "settings.ms365_connector.meeting_transcript": {
        "en": "Meeting transcript", "ja": "会議の文字起こし", "vi": "Meeting transcript"},
    "settings.ms365_allow_internet": {
        "en": "Allow external internet access", "ja": "外部インターネットアクセスを許可",
        "vi": "Cho phép truy cập Internet bên ngoài"},
    "settings.ms365_internet_off_hint": {
        "en": ("External internet access is OFF — every connector was turned off to avoid "
               "leaking data outside. Turn it back on, then re-tick the connectors you want."),
        "ja": ("外部インターネットアクセスがオフです — データが外部に漏れないよう、すべてのコネクタ"
               "がオフになりました。再度オンにしてから、必要なコネクタを選び直してください。"),
        "vi": ("Đã tắt truy cập Internet bên ngoài — mọi connector đã tự tắt để tránh rò rỉ "
                "thông tin ra ngoài. Bật lại rồi tick lại từng connector muốn dùng.")},
    "settings.ms365_signin_btn": {"en": "Sign in with Microsoft", "ja": "Microsoft でサインイン",
                                  "vi": "Đăng nhập Microsoft"},
    "settings.ms365_signout_btn": {"en": "Sign out", "ja": "サインアウト", "vi": "Đăng xuất"},
    "settings.ms365_not_signed_in": {
        "en": "Not signed in to Microsoft 365.", "ja": "Microsoft 365 にサインインしていません。",
        "vi": "Chưa đăng nhập Microsoft 365."},
    "settings.ms365_signed_in_as": {
        "en": "Signed in as {user}", "ja": "{user} としてサインイン中",
        "vi": "Đã đăng nhập với {user}"},
    "settings.ms365_missing_ids": {
        "en": "Enter the Tenant ID and Client ID first.", "ja": "先に Tenant ID と Client ID を入力してください。",
        "vi": "Hãy nhập Tenant ID và Client ID trước."},
    "settings.ms365_signing_in": {
        "en": "Starting sign-in…", "ja": "サインインを開始しています…", "vi": "Đang bắt đầu đăng nhập…"},
    "settings.ms365_signin_failed": {
        "en": "Sign-in failed: {err}", "ja": "サインインに失敗しました: {err}",
        "vi": "Đăng nhập thất bại: {err}"},
    "settings.ms365_teams_link_label": {
        "en": "Or just paste a Teams channel/chat link — no ID needed:",
        "ja": "または Teams のチャネル/チャットのリンクを貼り付けるだけ — ID は不要です:",
        "vi": "Hoặc chỉ cần paste link kênh/chat Teams — không cần ID:"},
    "settings.ms365_teams_link_placeholder": {
        "en": "Paste a link from Teams ('Get link to channel' or a message's 'Copy link')",
        "ja": "Teams のリンクを貼り付け（「チャネルへのリンクを取得」またはメッセージの「リンクをコピー」）",
        "vi": "Dán link từ Teams ('Get link to channel' hoặc 'Copy link' của 1 tin nhắn)"},
    "settings.ms365_teams_connect_btn": {"en": "Connect", "ja": "接続", "vi": "Kết nối"},
    "settings.ms365_teams_not_connected": {
        "en": "No Teams chat/channel connected yet.", "ja": "Teams のチャット/チャネルはまだ接続されていません。",
        "vi": "Chưa kết nối chat/kênh Teams nào."},
    "settings.ms365_teams_connected_channel": {
        "en": "Connected to a Teams channel.", "ja": "Teams のチャネルに接続済みです。",
        "vi": "Đã kết nối vào một kênh Teams."},
    "settings.ms365_teams_connected_chat": {
        "en": "Connected to a Teams chat.", "ja": "Teams のチャットに接続済みです。",
        "vi": "Đã kết nối vào một đoạn chat Teams."},
    "settings.ms365_teams_link_missing": {
        "en": "Paste a Teams link first.", "ja": "先に Teams のリンクを貼り付けてください。",
        "vi": "Hãy dán link Teams trước."},
    "settings.ms365_teams_connecting": {
        "en": "Connecting…", "ja": "接続しています…", "vi": "Đang kết nối…"},
    "settings.ms365_teams_connect_failed": {
        "en": "Connect failed: {err}", "ja": "接続に失敗しました: {err}",
        "vi": "Kết nối thất bại: {err}"},
    "settings.ms365_teams_intro_message": {
        "en": "Hi, I'm the Cowork agent — just connected to this chat/channel.",
        "ja": "こんにちは、Cowork エージェントです — このチャット/チャネルに接続しました。",
        "vi": "Xin chào, mình là Cowork agent — vừa kết nối vào chat/kênh này."},
    "settings.group.agent_security": {
        "en": "Agent Security (AI)", "ja": "エージェント セキュリティ（AI）",
        "vi": "Agent Security (AI)"},
    "settings.group.sandbox": {
        "en": "Sandbox Security Layer", "ja": "サンドボックス セキュリティ層",
        "vi": "Sandbox Security Layer"},
    "settings.sandbox_confirm_commands": {
        "en": "Confirm before Cowork runs a command",
        "ja": "Cowork がコマンドを実行する前に確認する",
        "vi": "Xác nhận trước khi Cowork chạy lệnh"},
    "settings.sandbox_confirm_commands_tooltip": {
        "en": ("Shows an Approve/Reject dialog before run_command/install_package "
               "executes in Cowork — off by default (auto-run), same as before."),
        "ja": "Cowork で run_command/install_package を実行する前に承認/拒否ダイアログを表示します — "
              "デフォルトはオフ（自動実行）で、これまでと同じです。",
        "vi": "Hiện hộp thoại Duyệt/Từ chối trước khi Cowork chạy run_command/install_package — "
              "mặc định tắt (tự chạy), giống hành vi cũ."},
    "settings.sandbox_block_network": {
        "en": "Block network for agent-run commands",
        "ja": "エージェントが実行するコマンドのネットワークをブロック",
        "vi": "Chặn mạng cho lệnh do agent chạy"},
    "settings.allow_url_fetch": {
        "en": "Allow the agent to fetch URLs (web pages, SharePoint / OneDrive links)",
        "ja": "エージェントによるURL取得を許可（Webページ、SharePoint / OneDriveリンク）",
        "vi": "Cho phép agent lấy dữ liệu từ URL (trang web, link SharePoint / OneDrive)"},
    "settings.allow_url_fetch_tooltip": {
        "en": ("Lets the agent's fetch_url tool read web pages, online documents and "
               "SharePoint/OneDrive share links to search & process them. Separate from "
               "'Block network' (which only sandboxes shell commands). Default: on."),
        "ja": "エージェントのfetch_urlツールがWebページ・オンライン文書・SharePoint/OneDrive共有リンクを"
              "読み取れるようにします。「ネットワークをブロック」（シェルコマンド用）とは別です。既定: オン。",
        "vi": ("Cho phép tool fetch_url của agent đọc trang web, tài liệu online và link chia sẻ "
               "SharePoint/OneDrive để tìm kiếm & xử lý. Tách biệt với 'Chặn mạng' (chỉ áp cho lệnh "
               "shell). Mặc định: bật.")},
    "settings.test_internet": {
        "en": "Test Internet", "ja": "インターネット接続テスト", "vi": "Kiểm tra Internet"},
    "settings.test_internet_tooltip": {
        "en": ("Live-checks the app's own outbound HTTPS path (the same one fetch_url uses) "
               "and reports the concrete reason if it can't reach the internet."),
        "ja": "アプリ自身の送信HTTPS経路（fetch_urlと同じ）を実際にテストし、インターネットに到達できない"
              "場合は具体的な理由を表示します。",
        "vi": ("Kiểm tra trực tiếp đường HTTPS ra ngoài của app (đúng đường mà fetch_url dùng) và "
               "báo lý do cụ thể nếu không truy cập được internet.")},
    "settings.testing_internet": {
        "en": "Testing internet access…", "ja": "インターネット接続をテスト中…",
        "vi": "Đang kiểm tra truy cập internet…"},
    "settings.sandbox_block_network_tooltip": {
        "en": ("Policy-level control (proxy env vars point at a black hole) — not a kernel "
               "firewall. Combine with the command whitelist above for defense in depth."),
        "ja": "ポリシーレベルの制御です（プロキシ環境変数をブラックホールに向ける）— カーネルレベルの"
              "ファイアウォールではありません。上のコマンドホワイトリストと併用してください。",
        "vi": "Kiểm soát ở tầng chính sách (trỏ biến môi trường proxy vào hố đen) — không phải "
              "firewall tầng kernel. Kết hợp với whitelist lệnh ở trên để phòng thủ nhiều lớp."},
    "settings.sandbox_unlimited": {"en": "Unlimited", "ja": "無制限", "vi": "Không giới hạn"},
    "settings.sandbox_cpu_label": {"en": "CPU limit", "ja": "CPU 制限", "vi": "Giới hạn CPU"},
    "settings.sandbox_memory_label": {"en": "Memory limit", "ja": "メモリ制限", "vi": "Giới hạn bộ nhớ"},
    "settings.sandbox_disk_label": {"en": "Disk I/O limit", "ja": "ディスク I/O 制限", "vi": "Giới hạn disk I/O"},
    "settings.sandbox_hint": {
        "en": ("Applies to every run_command/install_package the agent executes "
               "(Cowork, Code tab, and Schedule Task alike). 0 = unlimited. This layer is "
               "independent of \"Agent Security\" above — it still applies even while that "
               "toggle is off."),
        "ja": "エージェントが実行するすべての run_command/install_package に適用されます"
              "（Cowork、Code タブ、Schedule Task 共通）。0 = 無制限。この機能は上の「Agent "
              "Security」とは独立しており、そのトグルがオフの間も適用され続けます。",
        "vi": "Áp dụng cho mọi run_command/install_package mà agent chạy (Cowork, tab Code, "
              "và Schedule Task). 0 = không giới hạn. Lớp này độc lập với \"Agent Security\" "
              "ở trên — vẫn áp dụng ngay cả khi tắt Agent Security."},
    "settings.group.mcp": {"en": "MCP Servers", "ja": "MCP サーバー", "vi": "MCP Servers"},
    "settings.mcp_hint": {
        "en": ("Connect to external MCP (Model Context Protocol) servers — e.g. the official "
               "filesystem/GitHub/brave-search servers — and their tools become available to "
               "the agent alongside Microsoft 365 and the built-in file/command tools."),
        "ja": "外部の MCP（Model Context Protocol）サーバー（公式の filesystem/GitHub/brave-search "
              "サーバーなど）に接続すると、そのツールが Microsoft 365 や組み込みのファイル/コマンド"
              "ツールと並んでエージェントから利用できるようになります。",
        "vi": "Kết nối tới các MCP server bên ngoài (vd: server filesystem/GitHub/brave-search chính "
              "thức) — tool của chúng sẽ khả dụng cho agent cùng với Microsoft 365 và tool file/lệnh "
              "có sẵn."},
    "settings.mcp_add_btn": {"en": "Add server…", "ja": "サーバーを追加…", "vi": "Thêm server…"},
    "settings.mcp_edit_btn": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "settings.mcp_delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "settings.mcp_no_servers": {
        "en": "(No MCP servers configured — click 'Add server…')",
        "ja": "（MCP サーバーが設定されていません。「サーバーを追加…」をクリック）",
        "vi": "(Chưa cấu hình MCP server nào — bấm 'Thêm server…')"},
    "settings.mcp_delete_confirm": {
        "en": "Remove MCP server \"{name}\"?", "ja": "MCP サーバー「{name}」を削除しますか？",
        "vi": "Xóa MCP server \"{name}\"?"},
    "mcp.add_title": {"en": "Add MCP server", "ja": "MCP サーバーを追加", "vi": "Thêm MCP server"},
    "mcp.edit_title": {"en": "Edit MCP server", "ja": "MCP サーバーを編集", "vi": "Sửa MCP server"},
    "mcp.name_label": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "mcp.name_placeholder": {"en": "e.g. filesystem", "ja": "例: filesystem", "vi": "vd: filesystem"},
    "mcp.command_label": {"en": "Command", "ja": "コマンド", "vi": "Lệnh"},
    "mcp.command_placeholder": {"en": "e.g. npx", "ja": "例: npx", "vi": "vd: npx"},
    "mcp.args_label": {"en": "Arguments", "ja": "引数", "vi": "Tham số"},
    "mcp.args_placeholder": {
        "en": "e.g. -y @modelcontextprotocol/server-filesystem C:\\Data",
        "ja": "例: -y @modelcontextprotocol/server-filesystem C:\\Data",
        "vi": "vd: -y @modelcontextprotocol/server-filesystem C:\\Data"},
    "mcp.hint": {
        "en": ("The server is launched as a subprocess and talked to over stdio (the standard "
               "MCP transport) — the SAME way Claude Desktop/other MCP clients connect to it."),
        "ja": "サーバーはサブプロセスとして起動され、stdio（標準の MCP トランスポート）で通信します"
              "— Claude Desktop など他の MCP クライアントと同じ方式です。",
        "vi": "Server được khởi chạy như 1 subprocess và giao tiếp qua stdio (giao thức MCP chuẩn) "
              "— giống cách Claude Desktop hay các MCP client khác kết nối tới nó."},

    # ---- settings_dialog.py / ext_connector_dialog.py: External Connectors (CAD/CAE/Office) ----
    "settings.group.ext": {
        "en": "Connectors (MCP)",
        "ja": "コネクタ（MCP）",
        "vi": "Connectors (MCP)"},
    "settings.ext_moved_hint": {
        "en": "Connector (MCP / REST-API) setup moved to Monitoring → Tools → Connector.",
        "ja": "コネクター（MCP / REST-API）の設定は「モニタリング → ツール → Connector」へ移動しました。",
        "vi": "Thiết lập Connector (MCP / REST-API) đã chuyển sang Monitoring → Công cụ → Connector."},
    "settings.ext_hint": {
        "en": ("One place for every external tool source — grouped as CAD (NX/CATIA/SolidWorks/"
               "AutoCAD), CAE (ANSA/ABAQUS/HyperWorks/ANSYS), MS365 (Microsoft 365/OneDrive/"
               "SharePoint) and Other (any generic MCP server). MS365 auto-connects via the built-in "
               "server once you sign in; for the rest, point each connector at an MCP server you "
               "already have or a REST API it exposes (no vendor SDK is bundled)."),
        "ja": "外部ツール接続を1か所に集約 — CAD（NX/CATIA/SolidWorks/AutoCAD）、CAE（ANSA/ABAQUS/"
              "HyperWorks/ANSYS）、MS365（Microsoft 365/OneDrive/SharePoint）、Other（汎用 MCP サーバー）。"
              "MS365 はサインインすると内蔵サーバーで自動接続。その他は既存の MCP サーバーまたは REST API を"
              "指定してください（ベンダー SDK は同梱しません）。",
        "vi": "Một nơi duy nhất cho mọi nguồn tool ngoài — nhóm theo CAD (NX/CATIA/SolidWorks/AutoCAD), "
              "CAE (ANSA/ABAQUS/HyperWorks/ANSYS), MS365 (Microsoft 365/OneDrive/SharePoint) và Other "
              "(MCP server bất kỳ). MS365 tự kết nối qua server tích hợp sau khi đăng nhập; còn lại bạn "
              "trỏ mỗi connector tới MCP server bạn đã có hoặc REST API nó cung cấp (không kèm SDK hãng nào)."},
    "settings.ext_add_btn": {"en": "Add connector…", "ja": "コネクタを追加…", "vi": "Thêm connector…"},
    "settings.ext_edit_btn": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "settings.ext_delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "settings.ext_delete_confirm": {
        "en": "Remove connector \"{name}\"?", "ja": "コネクタ「{name}」を削除しますか？",
        "vi": "Xóa connector \"{name}\"?"},
    "ext.add_title": {"en": "Add connector", "ja": "コネクタを追加", "vi": "Thêm connector"},
    "ext.edit_title": {"en": "Edit connector", "ja": "コネクタを編集", "vi": "Sửa connector"},
    "ext.preset_label": {"en": "App", "ja": "アプリ", "vi": "Ứng dụng"},
    "ext.preset_custom": {"en": "(Custom…)", "ja": "（カスタム…）", "vi": "(Tuỳ chỉnh…)"},
    "ext.name_label": {"en": "Display name", "ja": "表示名", "vi": "Tên hiển thị"},
    "ext.name_placeholder": {"en": "e.g. NX (Site A)", "ja": "例: NX（サイトA）", "vi": "vd: NX (Site A)"},
    "ext.mode_label": {"en": "Connection type", "ja": "接続方式", "vi": "Kiểu kết nối"},
    "ext.mode_mcp": {"en": "MCP server (stdio)", "ja": "MCP サーバー（stdio）", "vi": "MCP server (stdio)"},
    "ext.mode_rest": {"en": "REST API", "ja": "REST API", "vi": "REST API"},
    "ext.mode_builtin": {"en": "built-in, auto-connect", "ja": "内蔵・自動接続", "vi": "tích hợp, tự kết nối"},
    "settings.ms365_signin_btn": {"en": "Sign in to Microsoft 365", "ja": "Microsoft 365 にサインイン",
                                  "vi": "Đăng nhập Microsoft 365"},
    "settings.ms365_signout_btn": {"en": "Sign out", "ja": "サインアウト", "vi": "Đăng xuất"},
    "settings.ms365_signed_in": {"en": "Microsoft 365: signed in as {who}",
                                 "ja": "Microsoft 365: {who} でサインイン中",
                                 "vi": "Microsoft 365: đã đăng nhập ({who})"},
    "settings.ms365_signed_out": {
        "en": "Microsoft 365: not signed in — one click, no Tenant/Client ID needed.",
        "ja": "Microsoft 365: 未サインイン — ワンクリック、テナント/クライアント ID 不要。",
        "vi": "Microsoft 365: chưa đăng nhập — 1 cú click, không cần Tenant/Client ID."},
    "settings.ms365_signing_in": {
        "en": "Microsoft 365: opening sign-in… follow the code prompt.",
        "ja": "Microsoft 365: サインインを開始中… コードの案内に従ってください。",
        "vi": "Microsoft 365: đang mở đăng nhập… làm theo hướng dẫn mã code."},
    "settings.ms365_code_hint": {
        "en": ("The sign-in page opened in your browser (<a href='{url}'>{url}</a>) and the code "
               "below was copied to your clipboard — just paste it, then sign in with your Microsoft "
               "account. This window closes automatically when sign-in completes."),
        "ja": ("ブラウザでサインインページ（<a href='{url}'>{url}</a>）を開きました。下のコードはクリップボードに"
               "コピー済みです — 貼り付けて Microsoft アカウントでサインインしてください。完了すると自動で閉じます。"),
        "vi": ("Trang đăng nhập đã mở trong trình duyệt (<a href='{url}'>{url}</a>) và mã bên dưới đã được "
               "copy vào clipboard — chỉ cần dán, rồi đăng nhập bằng tài khoản Microsoft. Cửa sổ này tự đóng "
               "khi đăng nhập xong.")},
    "settings.ms365_copy_code": {"en": "Copy code", "ja": "コードをコピー", "vi": "Copy mã"},
    "settings.ms365_open_link": {"en": "Open link", "ja": "リンクを開く", "vi": "Mở link"},
    "settings.ms365_local_connected": {
        "en": "OneDrive / SharePoint: auto-connected via local sync — no sign-in needed.\nSynced folder: {path}",
        "ja": "OneDrive / SharePoint: ローカル同期で自動接続 — サインイン不要。\n同期フォルダ: {path}",
        "vi": "OneDrive / SharePoint: tự động kết nối qua thư mục sync local — không cần đăng nhập.\nThư mục đã sync: {path}"},
    "settings.ms365_local_none": {
        "en": "OneDrive / SharePoint: no locally-synced OneDrive folder found. Install/sign in to "
              "the OneDrive desktop app and sync a folder, then reopen Settings.",
        "ja": "OneDrive / SharePoint: ローカル同期の OneDrive フォルダが見つかりません。OneDrive デスクトップ"
              "アプリでサインインしフォルダを同期してから、設定を開き直してください。",
        "vi": "OneDrive / SharePoint: chưa tìm thấy thư mục OneDrive sync trên máy. Cài/đăng nhập OneDrive "
              "desktop và sync một thư mục, rồi mở lại Settings."},
    "ext.command_label": {"en": "Command", "ja": "コマンド", "vi": "Lệnh"},
    "ext.command_placeholder": {"en": "e.g. python or npx", "ja": "例: python または npx", "vi": "vd: python hoặc npx"},
    "ext.args_label": {"en": "Arguments", "ja": "引数", "vi": "Tham số"},
    "ext.args_placeholder": {"en": "e.g. -m nx_mcp_server", "ja": "例: -m nx_mcp_server", "vi": "vd: -m nx_mcp_server"},
    "ext.base_url_label": {"en": "Base URL", "ja": "ベース URL", "vi": "Base URL"},
    "ext.base_url_placeholder": {
        "en": "e.g. https://cad-api.internal.company.com",
        "ja": "例: https://cad-api.internal.company.com",
        "vi": "vd: https://cad-api.internal.company.com"},
    "ext.api_key_label": {"en": "API key", "ja": "API キー", "vi": "API key"},
    "ext.auth_header_label": {"en": "Auth header name", "ja": "認証ヘッダー名", "vi": "Tên header xác thực"},
    "ext.auth_scheme_label": {"en": "Auth scheme", "ja": "認証スキーム", "vi": "Auth scheme"},
    "ext.test_btn": {"en": "Test connection", "ja": "接続テスト", "vi": "Kiểm tra kết nối"},
    "ext.err_no_command": {
        "en": "Enter a command first.", "ja": "先にコマンドを入力してください。", "vi": "Hãy nhập lệnh trước."},
    "ext.test_mcp_ok": {
        "en": "MCP server started and responded.", "ja": "MCP サーバーが起動し応答しました。",
        "vi": "MCP server đã khởi chạy và phản hồi."},

    "settings.sec_enabled": {
        "en": "Enable AI-assisted agent security guardrails",
        "ja": "AI 支援のエージェント セキュリティ ガードレールを有効化",
        "vi": "Bật các lớp bảo mật agent có AI hỗ trợ"},
    "settings.sec_hint": {
        "en": "Three independent layers: an AI reviews the user's request and "
              "attachment content against the rules below before the agent "
              "acts, and a whitelist + AI control-agent checks every "
              "run_command/install_package call. A violation always blocks "
              "the action and emails the admin below. Each AI check fails "
              "OPEN (allows) if the model itself can't be reached — a gateway "
              "hiccup must never make the agent unusable.",
        "ja": "3つの独立した層があります：エージェントが行動する前に、AI がユーザーの"
              "リクエストと添付ファイルの内容を下記のルールと照合してチェックし、"
              "ホワイトリストと AI コントロールエージェントがすべての "
              "run_command/install_package 呼び出しをチェックします。違反時は常に"
              "操作をブロックし、下記の管理者にメールで通知します。各 AI チェックは"
              "モデルに到達できない場合は「許可」側に倒れます（フェイルオープン）— "
              "ゲートウェイの一時的な不調でエージェントが使えなくなることがあっては"
              "なりません。",
        "vi": "Ba lớp độc lập: AI kiểm tra yêu cầu của người dùng và nội dung file "
              "đính kèm theo các rule bên dưới TRƯỚC khi agent hành động, và một "
              "whitelist + AI control-agent kiểm tra mọi lệnh run_command/"
              "install_package. Vi phạm sẽ luôn CHẶN hành động và gửi email cho "
              "admin bên dưới. Mỗi lớp kiểm tra bằng AI sẽ MẶC ĐỊNH CHO PHÉP nếu "
              "không gọi được model — một sự cố gateway tạm thời không được phép "
              "làm agent ngừng hoạt động."},
    "settings.sec_validate_prompt": {
        "en": "Validate the user's request (prompt) before acting",
        "ja": "行動する前にユーザーのリクエスト（プロンプト）を検証",
        "vi": "Validate yêu cầu (prompt) của người dùng trước khi hành động"},
    "settings.sec_validate_attachments": {
        "en": "Scan attachment/file content for malicious payloads",
        "ja": "添付/ファイルの内容に悪意あるペイロードがないかスキャン",
        "vi": "Scan nội dung file đính kèm để phát hiện nội dung độc hại"},
    "settings.sec_validate_commands": {
        "en": "Check run_command / install_package against a whitelist",
        "ja": "run_command / install_package をホワイトリストと照合",
        "vi": "Kiểm tra run_command / install_package theo whitelist"},
    "settings.sec_command_ai_check": {
        "en": "Also let an AI control-agent judge commands not covered by the whitelist",
        "ja": "ホワイトリストに含まれないコマンドは AI コントロールエージェントにも判定させる",
        "vi": "Cho AI control-agent xét thêm các lệnh whitelist chưa liệt kê"},
    "settings.sec_whitelist_label": {"en": "Command whitelist", "ja": "コマンド ホワイトリスト", "vi": "Whitelist lệnh"},
    "settings.sec_whitelist_placeholder": {
        "en": "One regex pattern per line, e.g. ^pip install\\n^pytest\\n^git ",
        "ja": "1行に1つの正規表現、例: ^pip install\\n^pytest\\n^git ",
        "vi": "Mỗi dòng 1 regex, vd: ^pip install\\n^pytest\\n^git "},
    "settings.sec_whitelist_empty_warning": {
        "en": "Empty whitelist + AI check off = every command is BLOCKED "
              "(fail-closed) — add a pattern above or turn AI check back on.",
        "ja": "ホワイトリストが空でAIチェックも無効の場合、すべてのコマンドが"
              "ブロックされます（フェイルクローズ）。上にパターンを追加するか"
              "AIチェックを再度有効にしてください。",
        "vi": "Whitelist trống + tắt AI-check = MỌI lệnh sẽ bị CHẶN hết "
              "(fail-closed) — hãy thêm pattern ở trên hoặc bật lại AI-check."},
    "settings.sec_onedrive_label": {"en": "OneDrive rules link", "ja": "OneDrive ルールへのリンク", "vi": "Link OneDrive chứa rule"},
    "settings.sec_onedrive_placeholder": {
        "en": "(optional) sharing link to an admin-authored .md rules document",
        "ja": "（任意）管理者が作成した .md ルール文書への共有リンク",
        "vi": "(tuỳ chọn) link chia sẻ tới file .md rule do admin soạn"},
    "settings.sec_admin_email_label": {"en": "Admin email", "ja": "管理者メール", "vi": "Email admin"},
    "settings.sec_admin_email_placeholder": {
        "en": "admin@yourcompany.com — receives violation alerts via Microsoft 365",
        "ja": "admin@yourcompany.com — Microsoft 365 経由で違反アラートを受信",
        "vi": "admin@yourcompany.com — nhận cảnh báo vi phạm qua Microsoft 365"},
    "settings.sec_rules_path_hint": {
        "en": "Local admin rules file (optional, edited directly, always applied): {path}",
        "ja": "ローカルの管理者ルールファイル（任意・直接編集・常に適用）: {path}",
        "vi": "File rule admin cục bộ (tuỳ chọn, sửa trực tiếp, luôn được áp dụng): {path}"},
    "settings.group.history": {"en": "Conversation history", "ja": "会話履歴", "vi": "Lịch sử hội thoại"},
    "settings.history_local": {"en": "Local (this PC)", "ja": "ローカル（このPC）", "vi": "Local (máy này)"},
    "settings.history_onedrive": {"en": "OneDrive", "ja": "OneDrive", "vi": "OneDrive"},
    "settings.location": {"en": "Location", "ja": "保存先", "vi": "Nơi lưu"},
    "settings.folder": {"en": "Folder", "ja": "フォルダ", "vi": "Thư mục"},
    "settings.folder_placeholder": {
        "en": "(optional) specific folder — leave empty for default",
        "ja": "（任意）特定のフォルダ ― 空欄で既定値",
        "vi": "(tuỳ chọn) thư mục cụ thể — để trống dùng mặc định"},
    "settings.browse": {"en": "Browse…", "ja": "参照…", "vi": "Duyệt…"},
    "settings.autosave": {"en": "Auto-save history after each turn", "ja": "各ターン後に履歴を自動保存", "vi": "Tự động lưu lịch sử sau mỗi lượt"},
    "settings.group.cowork": {"en": "Cowork", "ja": "Cowork", "vi": "Cowork"},
    "settings.max_parallel": {"en": "Max parallel conversations", "ja": "同時実行する会話数の上限", "vi": "Số hội thoại chạy song song tối đa"},
    "settings.parallel_suffix": {"en": "  conversations at once", "ja": "  件を同時実行", "vi": "  hội thoại cùng lúc"},
    "settings.parallel_tooltip": {
        "en": ("How many conversations run in parallel. Within one conversation messages always "
               "run one at a time (queued); only different conversations run in parallel."),
        "ja": ("並列実行する会話数です。1つの会話内のメッセージは常に1件ずつ（キュー）実行され、"
               "異なる会話同士のみ並列に実行されます。"),
        "vi": ("Số cuộc trò chuyện chạy song song. Trong MỘT cuộc trò chuyện, tin nhắn luôn "
               "chạy lần lượt (xếp hàng) để không bị trộn lẫn; chỉ các cuộc trò chuyện khác "
               "nhau mới chạy song song.")},
    "settings.group.attachments": {"en": "Attachments", "ja": "添付ファイル", "vi": "Tệp đính kèm"},
    "settings.max_files": {"en": "Max files", "ja": "最大ファイル数", "vi": "Số tệp tối đa"},
    "settings.max_files_suffix": {"en": "  files / message", "ja": "  件 / メッセージ", "vi": "  tệp / tin nhắn"},
    "settings.max_files_tooltip": {
        "en": "Maximum number of files attachable to one message.",
        "ja": "1メッセージに添付できるファイル数の上限。",
        "vi": "Số tệp tối đa đính kèm vào một tin nhắn."},
    "settings.max_per_file": {"en": "Max per file", "ja": "ファイルあたりの上限", "vi": "Giới hạn mỗi tệp"},
    "settings.max_per_file_suffix": {"en": "  K tokens / file", "ja": "  Kトークン / ファイル", "vi": "  K tokens / tệp"},
    "settings.max_per_file_tooltip": {
        "en": ("Limits how much of each attached file's content is added to the prompt; anything "
               "beyond this is truncated (fewer tokens, avoids exceeding the context limit)."),
        "ja": "各添付ファイルの内容をプロンプトに含める量の上限。超過分は切り捨てられます（トークン削減、コンテキスト超過回避）。",
        "vi": ("Giới hạn nội dung mỗi tệp đính kèm đưa vào prompt; phần vượt sẽ bị cắt "
               "(giảm token, tránh lỗi vượt context).")},
    "settings.group.structure": {"en": "GraphRAG", "ja": "GraphRAG", "vi": "GraphRAG"},
    "settings.group.sandbox_limits": {"en": "Sandbox resource limits", "ja": "サンドボックスのリソース上限",
                                      "vi": "Giới hạn tài nguyên Sandbox"},
    "settings.max_nodes": {"en": "Max nodes", "ja": "最大ノード数", "vi": "Số node tối đa"},
    "settings.unlimited": {"en": "Unlimited", "ja": "無制限", "vi": "Không giới hạn"},
    "settings.nodes_suffix": {"en": "  nodes", "ja": "  ノード", "vi": "  node"},
    "settings.nodes_tooltip": {
        "en": ("Cap the number of nodes in the Structure graph (0 = unlimited). "
               "A lower cap speeds up scanning/layout for large folders."),
        "ja": "構造グラフのノード数上限（0＝無制限）。大きなフォルダでは低い値の方が高速です。",
        "vi": "Giới hạn số node trong đồ thị Cấu trúc (0 = không giới hạn). Giá trị thấp hơn giúp quét/vẽ nhanh hơn với thư mục lớn."},
    "settings.max_edges": {"en": "Max edges", "ja": "最大エッジ数", "vi": "Số cạnh tối đa"},
    "settings.edges_suffix": {"en": "  edges", "ja": "  エッジ", "vi": "  cạnh"},
    "settings.edges_tooltip": {
        "en": "Cap the number of edges in the Structure graph (0 = unlimited).",
        "ja": "構造グラフのエッジ数上限（0＝無制限）。",
        "vi": "Giới hạn số cạnh trong đồ thị Cấu trúc (0 = không giới hạn)."},
    "settings.tip": {
        "en": "Tip: set your Internal Gateway URL + API key above, then pick a model.",
        "ja": "ヒント: 上で社内ゲートウェイの URL と API キーを設定してからモデルを選んでください。",
        "vi": "Mẹo: điền URL Gateway nội bộ + API key ở trên, rồi chọn model."},
    "settings.loading_models": {"en": "Loading models…", "ja": "モデルを読み込み中…", "vi": "Đang tải danh sách model…"},
    "settings.loaded_models": {
        "en": "Loaded {n} model(s) for {provider}.", "ja": "{provider} のモデルを {n} 件読み込みました。",
        "vi": "Đã tải {n} model cho {provider}."},
    "settings.load_failed": {"en": "Load failed: {err}", "ja": "読み込み失敗: {err}", "vi": "Tải thất bại: {err}"},
    "settings.load_models_error": {
        "en": "No models loaded — {err}", "ja": "モデルを読み込めませんでした — {err}",
        "vi": "Không tải được model nào — {err}"},
    "settings.load_models_error_unknown": {
        "en": "unknown error (check base URL / API key / network).",
        "ja": "不明なエラー（URL・APIキー・ネットワークを確認）。",
        "vi": "lỗi không xác định (kiểm tra base URL / API key / kết nối mạng)."},
    "settings.test_connection": {"en": "Test connection", "ja": "接続テスト", "vi": "Test kết nối"},
    "settings.test_connection_tooltip": {
        "en": "Check connectivity to this provider right now and show the real reason if it fails.",
        "ja": "このプロバイダーへの接続を今すぐ確認し、失敗した場合は本当の理由を表示します。",
        "vi": "Kiểm tra kết nối tới provider này ngay và hiện lý do thật nếu thất bại."},
    "settings.testing_connection": {"en": "Testing connection…", "ja": "接続を確認中…", "vi": "Đang kiểm tra kết nối…"},
    "settings.sending_test": {"en": "Sending test…", "ja": "テスト送信中…", "vi": "Đang gửi thử…"},
    "settings.test_failed": {"en": "Test failed: {err}", "ja": "テスト失敗: {err}", "vi": "Kiểm tra thất bại: {err}"},
    "settings.pick_hist_dir": {"en": "Choose history folder", "ja": "履歴フォルダを選択", "vi": "Chọn thư mục lưu lịch sử"},

    # ---- skills_dialog.py -----------------------------------------------
    "skills.edit_title": {"en": "Edit skill", "ja": "スキルを編集", "vi": "Sửa skill"},
    "skills.add_title": {"en": "Add skill", "ja": "スキルを追加", "vi": "Thêm skill"},
    "skills.name_label": {"en": "Skill name", "ja": "スキル名", "vi": "Tên skill"},
    "skills.name_placeholder": {
        "en": "e.g. Always write unit tests", "ja": "例：常に単体テストを書く", "vi": "vd. Luôn viết unit test"},
    "skills.desc_label": {"en": "Short description (optional)", "ja": "簡単な説明（任意）", "vi": "Mô tả ngắn (tuỳ chọn)"},
    "skills.instructions_label": {"en": "Instructions for the agent", "ja": "エージェントへの指示", "vi": "Hướng dẫn cho agent"},
    "skills.gen_from_desc": {"en": "Generate from description", "ja": "説明文から生成", "vi": "Tạo từ mô tả"},
    "skills.gen_from_desc_tooltip": {
        "en": "Use the AI agent to draft the instructions from the short description",
        "ja": "AI エージェントで短い説明から指示文の下書きを生成します",
        "vi": "Dùng AI để soạn hướng dẫn từ mô tả ngắn"},
    "skills.instructions_placeholder": {
        "en": "Describe the rules / guidance the agent must follow…",
        "ja": "エージェントが従うべきルール/ガイドラインを記述…",
        "vi": "Mô tả các quy tắc/hướng dẫn mà agent phải tuân theo…"},
    "skills.generating": {"en": "Generating…", "ja": "生成中…", "vi": "Đang tạo…"},
    "skills.title": {"en": "Skills", "ja": "スキル", "vi": "Skills"},
    "skills.hint": {
        "en": "Tick to enable a skill. Enabled skills are followed by the agent.",
        "ja": "チェックでスキルを有効化。有効なスキルはエージェントが従います。",
        "vi": "Tick để bật skill. Skill đang bật sẽ được agent tuân theo."},
    "skills.auto_generate": {"en": "Auto-generate", "ja": "自動生成", "vi": "Tự động tạo"},
    "skills.auto_generate_tooltip": {
        "en": ("Describe a skill in one line and let the AI draft the whole skill "
               "(name, description and instructions) for you to review."),
        "ja": "1行でスキルを説明すると、AI が名前・説明・指示文をまとめて下書きします。",
        "vi": "Mô tả skill trong 1 dòng, AI sẽ tự soạn cả skill (tên, mô tả, hướng dẫn) để bạn xem lại."},
    "skills.import_btn": {"en": "Import…", "ja": "インポート…", "vi": "Nhập…"},
    "skills.import_tooltip": {
        "en": "Import an external skill from a .skill, .json, .md or .txt file",
        "ja": ".skill / .json / .md / .txt ファイルから外部スキルをインポート",
        "vi": "Nhập skill từ file .skill, .json, .md hoặc .txt"},
    "skills.edit_btn": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "skills.delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "skills.close_btn": {"en": "Close", "ja": "閉じる", "vi": "Đóng"},
    "skills.no_skills": {
        "en": "(No skills yet — click ' Auto-generate' or 'Import…')",
        "ja": "（スキルはまだありません。「 自動生成」または「インポート…」をクリック）",
        "vi": "(Chưa có skill nào — bấm ' Tự động tạo' hoặc 'Nhập…')"},
    "skills.auto_generate_title": {"en": "Auto-generate skill", "ja": "スキルを自動生成", "vi": "Tự động tạo skill"},
    "skills.auto_generate_unavailable": {
        "en": "AI generation isn't available right now.", "ja": "現在 AI 生成は利用できません。",
        "vi": "Tính năng tạo bằng AI hiện chưa dùng được."},
    "skills.auto_generate_prompt": {
        "en": "Describe the skill you want (what should the agent do?):",
        "ja": "欲しいスキルを説明してください（エージェントに何をさせたいか）:",
        "vi": "Mô tả skill bạn muốn (agent nên làm gì?):"},
    "skills.auto_generate_failed": {
        "en": "Couldn't generate a skill. Check the AI provider in Settings, or add one manually.",
        "ja": "スキルを生成できませんでした。設定の AI プロバイダーを確認するか、手動で追加してください。",
        "vi": "Không tạo được skill. Kiểm tra lại provider AI trong Settings, hoặc tự thêm thủ công."},
    "skills.import_dialog_title": {"en": "Import skill", "ja": "スキルをインポート", "vi": "Nhập skill"},
    "skills.import_dialog_filter": {
        "en": "Skills (*.skill *.json *.md *.txt *.yaml *.yml *.zip);;All files (*.*)",
        "ja": "スキル (*.skill *.json *.md *.txt *.yaml *.yml *.zip);;すべてのファイル (*.*)",
        "vi": "Skill (*.skill *.json *.md *.txt *.yaml *.yml *.zip);;Tất cả file (*.*)"},
    "skills.import_failed": {"en": "Could not import: {err}", "ja": "インポートできませんでした: {err}", "vi": "Không nhập được: {err}"},
    "skills.export_btn": {"en": "Export .md", "ja": ".md エクスポート", "vi": "Xuất .md"},
    "skills.export_tooltip": {
        "en": "Export the selected skill to a Markdown (.md) file",
        "ja": "選択したスキルを Markdown (.md) ファイルに書き出します",
        "vi": "Xuất skill đang chọn ra file Markdown (.md)"},
    "skills.export_pick": {
        "en": "Select a skill in the list first, then click Export .md.",
        "ja": "先にリストでスキルを選択してから「.md エクスポート」を押してください。",
        "vi": "Hãy chọn một skill trong danh sách trước, rồi bấm Xuất .md."},
    "skills.export_dialog_title": {
        "en": "Export skill to Markdown", "ja": "スキルを Markdown に書き出す",
        "vi": "Xuất skill ra Markdown"},
    "skills.export_dialog_filter": {
        "en": "Markdown (*.md);;All files (*.*)", "ja": "Markdown (*.md);;すべてのファイル (*.*)",
        "vi": "Markdown (*.md);;Tất cả file (*.*)"},
    "skills.export_done": {
        "en": "Exported to {path}", "ja": "{path} に書き出しました", "vi": "Đã xuất ra {path}"},
    "skills.export_failed": {
        "en": "Could not export: {err}", "ja": "書き出せませんでした: {err}", "vi": "Không xuất được: {err}"},
    "skills.duplicate_btn": {"en": "Duplicate", "ja": "複製", "vi": "Nhân bản"},
    "skills.duplicate_tooltip": {
        "en": "Duplicate the selected skill (a copy you can rename and edit)",
        "ja": "選択したスキルを複製します（名前を変更・編集できるコピー）",
        "vi": "Nhân bản skill đang chọn (bản sao có thể đổi tên và chỉnh sửa)"},
    "skills.copy_name": {"en": "{name} (copy)", "ja": "{name}（コピー）", "vi": "{name} (bản sao)"},
    "skills.from_template": {"en": "From template file…", "ja": "テンプレートファイルから…", "vi": "Từ file template…"},
    "skills.from_template_tooltip": {
        "en": ("Analyze a .pptx/.xlsx template's layout, fonts, colors and formatting "
               "and draft a skill so future generated files match it."),
        "ja": ".pptx/.xlsx テンプレートのレイアウト・フォント・色・書式を解析し、"
              "今後生成するファイルがそれに合うようスキルを下書きします。",
        "vi": "Phân tích layout/font/màu/định dạng của file template .pptx/.xlsx, soạn skill để các file tạo sau khớp với nó."},
    "skills.from_template_title": {
        "en": "Generate skill from template", "ja": "テンプレートからスキルを生成",
        "vi": "Tạo skill từ template"},
    "skills.from_template_dialog_title": {
        "en": "Select a template file", "ja": "テンプレートファイルを選択",
        "vi": "Chọn file template"},
    "skills.from_template_dialog_filter": {
        "en": "PowerPoint/Excel (*.pptx *.xlsx *.xlsm);;All files (*.*)",
        "ja": "PowerPoint/Excel (*.pptx *.xlsx *.xlsm);;すべてのファイル (*.*)",
        "vi": "PowerPoint/Excel (*.pptx *.xlsx *.xlsm);;Tất cả file (*.*)"},
    "skills.from_template_failed": {
        "en": ("Couldn't analyze this template. Make sure it's a valid .pptx/.xlsx file "
               "and the AI provider in Settings works, or add the skill manually."),
        "ja": "このテンプレートを解析できませんでした。有効な .pptx/.xlsx ファイルか、"
              "設定の AI プロバイダーが動作しているか確認するか、手動でスキルを追加してください。",
        "vi": "Không phân tích được template này. Kiểm tra file .pptx/.xlsx hợp lệ và provider AI trong Settings hoạt động tốt, hoặc tự thêm skill thủ công."},

    # ---- flow_dialog.py -----------------------------------------------
    "flow.title": {"en": "Flow Management", "ja": "Flow Management", "vi": "Flow Management"},
    "flow.tab_flow": {"en": "Flow", "ja": "フロー", "vi": "Flow"},
    "flow.tab_agents": {"en": "Agents", "ja": "エージェント", "vi": "Agents"},
    "flow.tab_skills": {"en": "Skills", "ja": "スキル", "vi": "Skills"},
    "flow.template": {"en": "Template:", "ja": "テンプレート:", "vi": "Template:"},
    "flow.load_builtin": {"en": "Load Req→Demo template", "ja": "Req→Demo テンプレートを読込", "vi": "Tải template Req→Demo"},
    "flow.new": {"en": "New", "ja": "新規", "vi": "Mới"},
    "flow.delete_template": {"en": "Delete template", "ja": "テンプレートを削除", "vi": "Xóa template"},
    "flow.name_label": {"en": "Flow name", "ja": "フロー名", "vi": "Tên flow"},
    "flow.description_label": {"en": "Description", "ja": "説明", "vi": "Mô tả"},
    "flow.stages": {"en": "Stages", "ja": "ステージ", "vi": "Các bước"},
    "flow.remove_stage": {"en": "Remove stage", "ja": "ステージを削除", "vi": "Xóa bước"},
    "flow.stage_name": {"en": "Stage name", "ja": "ステージ名", "vi": "Tên bước"},
    "flow.hint": {"en": "Hint", "ja": "ヒント", "vi": "Gợi ý"},
    "flow.task_prompt": {"en": "Task (prompt)", "ja": "タスク（プロンプト）", "vi": "Nhiệm vụ (prompt)"},
    "flow.skill": {"en": "Skill", "ja": "スキル", "vi": "Skill"},
    "flow.agent": {"en": "AI provider", "ja": "AI プロバイダー", "vi": "AI provider"},
    "flow.model_label": {"en": "Agent:", "ja": "Agent:", "vi": "Agent:"},
    "flow.default_model": {"en": "(provider default)", "ja": "（プロバイダー既定）", "vi": "(mặc định của provider)"},
    "flow.gen_task_from_hint": {"en": "Generate task from hint", "ja": "ヒントからタスクを生成", "vi": "Tạo task từ gợi ý"},
    "flow.gen_task_tooltip": {
        "en": "Use the AI agent to expand the hint into a task prompt",
        "ja": "AI エージェントでヒントをタスクプロンプトに展開します",
        "vi": "Dùng AI để mở rộng gợi ý thành task prompt"},
    "flow.attachments": {"en": "Attachments", "ja": "添付ファイル", "vi": "Đính kèm"},
    "flow.attach_files": {"en": "Attach files…", "ja": "ファイルを添付…", "vi": "Đính kèm file…"},
    "flow.attach_files_count": {
        "en": "{n} file(s) attached", "ja": "{n} 件添付済み", "vi": "Đã đính kèm {n} file"},
    "flow.compact_after_run": {
        "en": "Compact after run", "ja": "実行後に圧縮", "vi": "Compact after run (nén sau khi chạy)"},
    "flow.compact_after_run_tooltip": {
        "en": "Trim older history right after this stage, freeing up token space for the next one",
        "ja": "このステージの直後に古い履歴を切り詰め、次のステージ用にトークン余裕を確保します",
        "vi": "Rút gọn lịch sử cũ ngay sau bước này để nhường chỗ token cho bước tiếp theo"},
    "flow.self_verify": {
        "en": "Self-verify before handoff", "ja": "引き渡し前に自己検証", "vi": "Self-verify trước khi bàn giao"},
    "flow.self_verify_tooltip": {
        "en": "Ask the agent to confirm the stage is actually complete before moving on",
        "ja": "次に進む前に、このステージが本当に完了しているかエージェントに確認させます",
        "vi": "Yêu cầu agent tự xác nhận đã hoàn thành đầy đủ trước khi qua bước sau"},
    "flow.review_retries": {
        "en": "Review-completeness retries", "ja": "完全性レビューの再試行回数", "vi": "Số lần review lại nếu chưa xong"},
    "flow.review_retries_tooltip": {
        "en": "If the self-check says the stage is incomplete, re-run it up to this many times (0 = off)",
        "ja": "自己チェックで未完了と判定された場合、この回数まで再実行します（0 = 無効）",
        "vi": "Nếu tự kiểm tra thấy chưa hoàn thành, chạy lại bước này tối đa số lần này (0 = tắt)"},
    "flow.parallel_agents": {
        "en": "Parallel sub-agents", "ja": "並列サブエージェント", "vi": "Sub-agent chạy song song"},
    "flow.subagent_name_placeholder": {"en": "Name (e.g. backend)", "ja": "名前（例: backend）", "vi": "Tên (vd backend)"},
    "flow.subagent_task_placeholder": {
        "en": "Task for this sub-agent (optional — falls back to the stage task)",
        "ja": "このサブエージェントのタスク（任意 — 未入力ならステージのタスクを使用）",
        "vi": "Nhiệm vụ của sub-agent này (tùy chọn — bỏ trống thì dùng task của bước)"},
    "flow.subagent_add": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "flow.subagent_remove": {"en": "Remove", "ja": "削除", "vi": "Xóa"},
    "flow.subagent_add_from_agent": {
        "en": "Add from Agent", "ja": "エージェントから追加", "vi": "Thêm từ Agent"},
    "flow.subagent_no_agents": {
        "en": "(no saved Agents — create one in the Agents tab)",
        "ja": "（保存済みのエージェントがありません — Agents タブで作成してください）",
        "vi": "(chưa có Agent nào — tạo ở tab Quản lý Agent)"},
    "flow.subagent_hint": {
        "en": ("Add 2+ sub-agents to make this a PARALLEL stage — they run concurrently, then "
               "the stage's own Task field is used to consolidate their results into one."),
        "ja": ("サブエージェントを2つ以上追加すると、このステージは並列ステージになります — "
               "同時に実行され、その後ステージ自体のタスク欄で結果を1つに統合します。"),
        "vi": ("Thêm từ 2 sub-agent trở lên để bước này chạy SONG SONG — chúng chạy đồng thời, "
               "sau đó ô Task của chính bước này dùng để gộp kết quả lại thành một.")},
    "flow.add_stage": {"en": "Add stage", "ja": "ステージを追加", "vi": "Thêm bước"},
    "flow.update_stage": {"en": "Update stage", "ja": "ステージを更新", "vi": "Cập nhật bước"},
    "flow.save_template": {"en": "Save as template", "ja": "テンプレートとして保存", "vi": "Lưu làm template"},
    "flow.run": {"en": "Run flow", "ja": "フローを実行", "vi": "Chạy flow"},
    "flow.close": {"en": "Close", "ja": "閉じる", "vi": "Đóng"},
    "flow.none": {"en": "(none)", "ja": "（なし）", "vi": "(không có)"},
    "flow.default_agent": {"en": "Default", "ja": "デフォルト", "vi": "Mặc định"},
    "flow.select_template": {"en": "— select template —", "ja": "— テンプレートを選択 —", "vi": "— chọn template —"},
    "flow.new_flow_name": {"en": "New flow", "ja": "新しいフロー", "vi": "Flow mới"},
    "flow.default_name": {"en": "Flow", "ja": "フロー", "vi": "Flow"},

    # ---- agent_manager_tab.py -------------------------------------------
    "agentmgr.hint": {
        "en": "Create reusable Agent presets (name + task + provider) — pick them as "
              "parallel sub-agents from any Flow stage in the Code tab.",
        "ja": "再利用できるエージェントのプリセット（名前・タスク・プロバイダー）を作成します — "
              "Code タブの任意のフローステージから並列サブエージェントとして選択できます。",
        "vi": "Tạo sẵn các Agent (tên + nhiệm vụ + provider) để tái sử dụng — chọn làm "
              "sub-agent chạy song song từ bất kỳ bước Flow nào ở tab Code."},
    "agentmgr.list_label": {"en": "Saved agents", "ja": "保存済みエージェント", "vi": "Agent đã lưu"},
    "agentmgr.name_label": {"en": "Agent name", "ja": "エージェント名", "vi": "Tên agent"},
    "agentmgr.desc_label": {"en": "Description", "ja": "説明", "vi": "Mô tả"},
    "agentmgr.prompt_label": {"en": "Task (prompt)", "ja": "タスク（プロンプト）", "vi": "Nhiệm vụ (prompt)"},
    "agentmgr.gen_prompt_btn": {"en": "Generate from description", "ja": "説明から生成",
                                "vi": "Tạo prompt từ mô tả"},
    "agentmgr.gen_prompt_tooltip": {
        "en": "Use the AI agent to expand the name/description into a task prompt",
        "ja": "AI エージェントで名前・説明をタスクプロンプトに展開します",
        "vi": "Dùng AI để mở rộng tên/mô tả thành task prompt"},
    "agentmgr.provider_label": {"en": "AI provider", "ja": "AI プロバイダー", "vi": "Provider AI"},
    "agentmgr.new_btn": {"en": "New agent", "ja": "新規エージェント", "vi": "Agent mới"},
    "agentmgr.save_btn": {"en": "Save agent", "ja": "エージェントを保存", "vi": "Lưu agent"},
    "agentmgr.delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "agentmgr.delete_confirm": {
        "en": "Delete agent '{name}'?", "ja": "エージェント「{name}」を削除しますか？",
        "vi": "Xóa agent '{name}'?"},

    # ---- permission_dialog.py ------------------------------------------
    "permission.title": {"en": "Confirm action", "ja": "操作を確認", "vi": "Xác nhận thao tác"},
    "permission.default_action": {"en": "Action", "ja": "操作", "vi": "Thao tác"},
    "permission.subtitle_command": {
        "en": "The agent wants to run this command in the working folder:",
        "ja": "エージェントが作業フォルダで次のコマンドを実行しようとしています:",
        "vi": "Agent muốn chạy lệnh này trong thư mục làm việc:"},
    "permission.subtitle_diff": {
        "en": "The agent wants to change a file (diff below):",
        "ja": "エージェントがファイルを変更しようとしています（差分は下記）:",
        "vi": "Agent muốn thay đổi một tệp (xem diff bên dưới):"},
    "permission.subtitle_default": {
        "en": "The agent proposes an action:", "ja": "エージェントが操作を提案しています:",
        "vi": "Agent đề xuất một thao tác:"},
    "permission.approve": {"en": "Approve", "ja": "承認", "vi": "Duyệt"},
    "permission.reject": {"en": "Reject", "ja": "拒否", "vi": "Từ chối"},
    "permission.remember_whitelist": {
        "en": "Remember — add to the command whitelist",
        "ja": "記憶する — コマンドのホワイトリストに追加",
        "vi": "Ghi nhớ — thêm vào whitelist lệnh"},
    "permission.remember_whitelist_tooltip": {
        "en": "Future commands starting the same way will be auto-approved without asking again.",
        "ja": "同じように始まる今後のコマンドは、再確認なしで自動承認されます。",
        "vi": "Các lệnh sau bắt đầu giống vậy sẽ được tự động duyệt, không hỏi lại."},


    # ---- structure_graph_view.py ---------------------------------------
    "structure.path_placeholder": {"en": "Source / document folder", "ja": "ソース/ドキュメントフォルダ", "vi": "Thư mục source/tài liệu"},
    "structure.browse": {"en": "Browse…", "ja": "参照…", "vi": "Browse…"},
    "structure.mode_all": {"en": "All files", "ja": "すべてのファイル", "vi": "All files"},
    "structure.mode_code": {"en": "Code only", "ja": "コードのみ", "vi": "Code only"},
    "structure.mode_doc": {"en": "Docs only", "ja": "ドキュメントのみ", "vi": "Docs only"},
    "structure.project_none": {"en": "(no project — free path)", "ja": "（プロジェクトなし — 自由パス）", "vi": "(không gán project — path tự do)"},
    "structure.project_tooltip": {
        "en": "Lock the scan to a project's sandbox workspace — path becomes read-only and the "
              "Agent Q&A below follows that project's shared Instructions (safer, grounded answers).",
        "ja": "スキャン対象をプロジェクトのサンドボックスワークスペースに固定します — パスは読み取り専用になり、"
              "下のエージェントQ&Aはそのプロジェクトの共有指示に従います（より安全で根拠のある回答）。",
        "vi": "Khóa phạm vi quét vào đúng thư mục sandbox của 1 project — path chuyển sang chỉ đọc và "
              "khung hỏi-đáp Agent bên dưới sẽ theo Instructions chung của project đó (an toàn hơn, "
              "câu trả lời bám sát ngữ cảnh, giảm bịa đặt).",
    },
    "structure.scan": {"en": "Scan", "ja": "スキャン", "vi": "Scan"},
    "structure.export_png": {"en": "Export PNG", "ja": "PNG エクスポート", "vi": "Xuất PNG"},
    "structure.msgs_btn": {"en": "Messages", "ja": "メッセージ", "vi": "Tin nhắn"},
    "structure.graph_btn": {"en": "Graph", "ja": "グラフ", "vi": "Đồ thị"},
    "structure.msgs_tooltip": {
        "en": "Show all conversation messages grouped by day (as JSON).",
        "ja": "会話メッセージを日別にJSONで表示。",
        "vi": "Xem mọi message hội thoại nhóm theo ngày (dạng JSON)."},
    "structure.msgs_none": {"en": "No messages yet.", "ja": "メッセージがありません。",
                            "vi": "Chưa có message nào."},
    "structure.open_browser": {"en": "Open in browser", "ja": "ブラウザで開く", "vi": "Mở trong trình duyệt"},
    "structure.open_browser_tooltip": {
        "en": "Open the full interactive D3 graph in your default browser (works in every build, including the standalone .exe)",
        "ja": "既定のブラウザでフル機能の D3 グラフを開きます（スタンドアロン .exe を含むすべてのビルドで利用可能）",
        "vi": "Mở đồ thị D3 đầy đủ tính năng trong trình duyệt mặc định (dùng được ở mọi bản build, kể cả file .exe độc lập)",
    },
    "structure.opened_browser": {
        "en": "D3 graph opened in your browser at {url}",
        "ja": "ブラウザで D3 グラフを開きました: {url}",
        "vi": "Đã mở đồ thị D3 trong trình duyệt tại {url}",
    },
    "structure.cmem_ui_open": {"en": "Codebase Memory UI", "ja": "Codebase Memory UI", "vi": "Codebase Memory UI"},
    "structure.cmem_ui_back": {"en": "Back to D3 view", "ja": "D3 表示に戻る", "vi": "Về đồ thị D3"},
    "structure.cmem_ui_tooltip": {
        "en": "Open codebase-memory-mcp's own graph UI (Graph/Projects/Control) for the current scan path.",
        "ja": "現在のスキャンパスに対して codebase-memory-mcp 独自のグラフ UI（Graph/Projects/Control）を開きます。",
        "vi": "Mở UI đồ thị riêng của codebase-memory-mcp (Graph/Projects/Control) cho đường dẫn đang quét."},
    "structure.cmem_ui_starting": {
        "en": "Starting codebase-memory-mcp UI…", "ja": "codebase-memory-mcp の UI を起動中…",
        "vi": "Đang khởi động UI của codebase-memory-mcp…"},
    "structure.cmem_ui_opened_embedded": {
        "en": "codebase-memory-mcp UI loaded.", "ja": "codebase-memory-mcp の UI を読み込みました。",
        "vi": "Đã tải UI của codebase-memory-mcp."},
    "structure.cmem_ui_opened_browser": {
        "en": "codebase-memory-mcp UI opened in your browser at {url}",
        "ja": "ブラウザで codebase-memory-mcp の UI を開きました: {url}",
        "vi": "Đã mở UI của codebase-memory-mcp trong trình duyệt tại {url}"},
    "structure.cmem_ui_not_built": {
        "en": "This codebase-memory-mcp build has no embedded UI. Install the "
              "'codebase-memory-mcp-ui' release asset from the project's GitHub "
              "releases to use this view. ({err})",
        "ja": "この codebase-memory-mcp ビルドには UI が組み込まれていません。このビューを使うには "
              "GitHub リリースから 'codebase-memory-mcp-ui' をインストールしてください。({err})",
        "vi": "Bản build codebase-memory-mcp này không có UI nhúng. Cần cài "
              "release asset 'codebase-memory-mcp-ui' từ trang GitHub Releases của "
              "dự án để dùng chức năng này. ({err})"},
    "structure.cmem_ui_failed": {
        "en": "Could not open codebase-memory-mcp UI: {err}",
        "ja": "codebase-memory-mcp の UI を開けませんでした: {err}",
        "vi": "Không mở được UI của codebase-memory-mcp: {err}"},
    "structure.collapse_agent_tooltip": {"en": "Collapse the Agent panel", "ja": "エージェントパネルを折りたたむ", "vi": "Thu gọn bảng Agent"},
    "structure.expand_agent_tooltip": {
        "en": "Click to expand the Agent panel", "ja": "クリックしてエージェントパネルを展開",
        "vi": "Bấm để mở lại bảng Agent"},
    "structure.agent_header": {"en": "Agent — ask about the graph", "ja": "エージェント ― グラフについて質問", "vi": "Agent — hỏi về đồ thị"},
    "structure.ask_placeholder": {
        "en": "e.g. what calls main? which files define classes?",
        "ja": "例: main を呼んでいるのは？クラスを定義しているファイルは？",
        "vi": "vd. cái gì gọi hàm main? file nào định nghĩa class?"},
    "structure.ask": {"en": "Ask", "ja": "質問", "vi": "Hỏi"},
    "structure.detail_placeholder": {
        "en": "Click a node to open its folder, or ask the agent about the graph.",
        "ja": "ノードをクリックするとフォルダを開きます。またはエージェントにグラフについて質問できます。",
        "vi": "Nhấp node để mở thư mục, hoặc hỏi agent về đồ thị."},
    "structure.pick_folder_title": {"en": "Choose folder", "ja": "フォルダを選択", "vi": "Chọn thư mục"},
    "structure.scanning": {"en": "Scanning structure…", "ja": "構造をスキャン中…", "vi": "Đang quét cấu trúc…"},
    "structure.scan_error": {"en": "Scan error: {err}", "ja": "スキャンエラー: {err}", "vi": "Lỗi khi quét: {err}"},
    "structure.graph_summary": {"en": "Graph: {nodes} nodes, {edges} edges.{note}", "ja": "グラフ: ノード {nodes} 個、エッジ {edges} 個。{note}", "vi": "Đồ thị: {nodes} node, {edges} cạnh.{note}"},
    "structure.truncated_note": {"en": "  (truncated — too many nodes)", "ja": "  （切り捨て：ノードが多すぎます）", "vi": "  (đã cắt bớt — quá nhiều node)"},
    "structure.export_title": {"en": "Export graph PNG", "ja": "グラフを PNG でエクスポート", "vi": "Xuất đồ thị ra PNG"},
    "structure.export_done": {"en": "Graph exported to {path}", "ja": "グラフを {path} にエクスポートしました", "vi": "Đã xuất đồ thị ra {path}"},
    "structure.export_failed": {"en": "Export failed: {err}", "ja": "エクスポート失敗: {err}", "vi": "Xuất thất bại: {err}"},
    "structure.scan_first": {"en": "Scan a graph first.", "ja": "先にグラフをスキャンしてください。", "vi": "Hãy Scan đồ thị trước."},
    "structure.related_sources": {
        "en": "Related files (click to open):",
        "ja": "関連ファイル（クリックで開く）:",
        "vi": "Tệp liên quan (bấm để mở):"},
    "structure.legend.dir": {"en": "Folder", "ja": "フォルダ", "vi": "Thư mục"},
    "structure.legend.file": {"en": "File", "ja": "ファイル", "vi": "Tệp"},
    "structure.legend.class": {"en": "Class", "ja": "クラス", "vi": "Class"},
    "structure.legend.function": {"en": "Function", "ja": "関数", "vi": "Function"},
    "structure.legend.method": {"en": "Method", "ja": "メソッド", "vi": "Method"},
    "structure.legend.module": {"en": "Module", "ja": "モジュール", "vi": "Module"},
    "structure.legend.section": {"en": "Section", "ja": "セクション", "vi": "Mục"},
    "structure.legend.json_key": {"en": "JSON key", "ja": "JSONキー", "vi": "Khóa JSON"},
    "structure.legend.entities": {"en": "Entities", "ja": "エンティティ", "vi": "Thực thể"},
    "structure.legend.relationships": {"en": "Relationships", "ja": "関係", "vi": "Quan hệ"},
    "structure.show_label": {"en": "Show label", "ja": "ラベル表示", "vi": "Hiện nhãn"},
    "structure.show_relationship": {
        "en": "Show relationship", "ja": "関係を表示", "vi": "Hiện quan hệ"},
    "structure.edge.contains": {"en": "contains", "ja": "含む", "vi": "chứa"},
    "structure.edge.defines": {"en": "defines", "ja": "定義", "vi": "định nghĩa"},
    "structure.edge.method": {"en": "method", "ja": "メソッド", "vi": "phương thức"},
    "structure.edge.imports": {"en": "imports", "ja": "インポート", "vi": "import"},
    "structure.edge.subsection": {"en": "subsection", "ja": "サブセクション", "vi": "mục con"},

    # ---- libreoffice_view.py -------------------------------------------
    "libreoffice.open_btn": {"en": "Open in LibreOffice", "ja": "LibreOffice で開く", "vi": "Mở bằng LibreOffice"},
    "libreoffice.not_found": {
        "en": ("LibreOffice was not found. Install LibreOffice (or set the "
               "SOFFICE_PATH environment variable) to view and edit documents here."),
        "ja": "LibreOffice が見つかりません。ここで文書を表示/編集するには LibreOffice をインストールするか、環境変数 SOFFICE_PATH を設定してください。",
        "vi": "Không tìm thấy LibreOffice. Hãy cài LibreOffice (hoặc đặt biến môi trường SOFFICE_PATH) để xem/sửa tài liệu tại đây."},
    "libreoffice.windows_only": {
        "en": "Embedding the editor is available on Windows. Click below to open this document in LibreOffice.",
        "ja": "エディタの埋め込みは Windows でのみ利用可能です。下のボタンで LibreOffice で開いてください。",
        "vi": "Nhúng trình soạn thảo chỉ khả dụng trên Windows. Bấm bên dưới để mở tài liệu bằng LibreOffice."},
    "libreoffice.start_failed": {"en": "Could not start LibreOffice ({err}).", "ja": "LibreOffice を起動できませんでした（{err}）。", "vi": "Không khởi động được LibreOffice ({err})."},
    "libreoffice.opening": {"en": "Opening the document in LibreOffice…", "ja": "LibreOffice で文書を開いています…", "vi": "Đang mở tài liệu bằng LibreOffice…"},
    "libreoffice.embed_failed": {
        "en": "Couldn't embed the LibreOffice window. You can open it in a separate window instead.",
        "ja": "LibreOffice ウィンドウを埋め込めませんでした。別ウィンドウで開くことができます。",
        "vi": "Không nhúng được cửa sổ LibreOffice. Bạn có thể mở nó ở cửa sổ riêng."},
    "libreoffice.embed_error": {"en": "Couldn't embed LibreOffice ({err}).", "ja": "LibreOffice を埋め込めませんでした（{err}）。", "vi": "Không nhúng được LibreOffice ({err})."},

    # ---- monitoring_tab.py (📊 Monitoring Dashboard) --------------------
    "monitoring.title": {"en": "Monitoring Dashboard", "ja": "モニタリングダッシュボード", "vi": "Bảng giám sát"},
    "monitoring.refresh": {"en": "Refresh", "ja": "更新", "vi": "Làm mới"},
    "monitoring.tab_security": {"en": "Security Events", "ja": "セキュリティイベント", "vi": "Sự kiện bảo mật"},
    "monitoring.tab_mcp": {"en": "MCP Call History", "ja": "MCP 呼び出し履歴", "vi": "Lịch sử gọi MCP"},
    "monitoring.tab_actions": {"en": "Action Logs", "ja": "アクションログ", "vi": "Nhật ký hành động"},
    "monitoring.tab_agents": {"en": "Agent Status", "ja": "エージェント状態", "vi": "Trạng thái Agent"},
    "monitoring.tab_accounts": {"en": "Accounts", "ja": "アカウント", "vi": "Tài khoản"},
    "monitoring.col_time": {"en": "Time", "ja": "時刻", "vi": "Thời gian"},
    "monitoring.col_role": {"en": "Agent Role", "ja": "エージェント役割", "vi": "Vai trò Agent"},
    "monitoring.col_name": {"en": "Action", "ja": "アクション", "vi": "Hành động"},
    "monitoring.col_result": {"en": "Result", "ja": "結果", "vi": "Kết quả"},
    "monitoring.col_detail": {"en": "Detail", "ja": "詳細", "vi": "Chi tiết"},
    "monitoring.col_account": {"en": "Account", "ja": "アカウント", "vi": "Tài khoản"},
    "monitoring.col_machine": {"en": "Machine", "ja": "マシン", "vi": "Máy"},
    "monitoring.col_agent": {"en": "Agent", "ja": "エージェント", "vi": "Agent"},
    "monitoring.col_active": {"en": "Active", "ja": "稼働中", "vi": "Đang chạy"},
    "monitoring.col_source": {"en": "Source", "ja": "ソース", "vi": "Nguồn"},
    "monitoring.active_n": {"en": "{n} running", "ja": "{n} 件実行中", "vi": "{n} đang chạy"},
    "monitoring.source_cowork": {
        "en": "Cowork tab's active turns", "ja": "Cowork タブの実行中ターン",
        "vi": "Lượt đang chạy của tab Cowork"},
    "monitoring.source_task": {
        "en": "Schedule Task's running tasks", "ja": "Schedule Task の実行中タスク",
        "vi": "Task đang chạy trong Schedule Task"},
    "monitoring.source_knowledge": {
        "en": "GraphRAG's Ask box", "ja": "GraphRAG の Ask ボックス", "vi": "Ô hỏi của GraphRAG"},
    "monitoring.source_code": {
        "en": "Runs inside a Task Agent run when the task type is Code",
        "ja": "タスクタイプが Code の場合、Task Agent の実行内で動作します",
        "vi": "Chạy bên trong một lượt Task Agent khi loại task là Code"},
    "monitoring.source_planner": {
        "en": "A phase inside a running Cowork/Task turn (update_plan) — not tracked separately",
        "ja": "実行中の Cowork/Task ターン内の一段階（update_plan）— 個別には追跡されません",
        "vi": "Một giai đoạn trong lượt Cowork/Task đang chạy (update_plan) — không theo dõi riêng"},
    "monitoring.source_reasoning": {
        "en": "The model's streamed reasoning within a running turn — not tracked separately",
        "ja": "実行中のターン内でモデルがストリーミングする推論 — 個別には追跡されません",
        "vi": "Luồng suy luận (reasoning) của model trong lượt đang chạy — không theo dõi riêng"},
    "monitoring.source_security": {
        "en": "Agent Security's prompt/attachment/command validation — runs inline on the active turn",
        "ja": "エージェントセキュリティのプロンプト/添付/コマンド検証 — 実行中のターン内でインライン実行",
        "vi": "Kiểm duyệt prompt/đính kèm/lệnh của Agent Security — chạy inline trong lượt đang chạy"},
    "monitoring.on": {"en": "On", "ja": "オン", "vi": "Bật"},
    "monitoring.off": {"en": "Off", "ja": "オフ", "vi": "Tắt"},

    # ---- monitoring_tab.py — Overview card dashboard ---------------------
    "monitoring.tab_overview": {"en": "Overview", "ja": "概要", "vi": "Tổng quan"},
    "monitoring.overview_usage_title": {
        "en": "Token Usage & Cost", "ja": "トークン使用量とコスト", "vi": "Sử dụng token & Chi phí"},
    "monitoring.overview_currency": {"en": "Currency:", "ja": "通貨:", "vi": "Tiền tệ:"},
    "monitoring.tab_agents_admin": {
        "en": "Agents Admin", "ja": "エージェント管理", "vi": "Agents Admin"},
    "monitoring.tab_tools": {"en": "Tools", "ja": "ツール", "vi": "Công cụ"},

    # ---- tools_admin_tab.py — govern built-in tools + Connectors/MCP -------
    "tools_admin.hint": {
        "en": "Enable or disable the built-in agent tools below. A tool toggled off is removed "
              "from the agent's toolset. MCP / REST-API connectors are set up in the Connector "
              "sub-tab.",
        "ja": "下の組み込みエージェントツールをオン/オフします。オフにしたツールはツールセットから除外"
              "されます。MCP / REST-APIコネクターは「Connector」サブタブで設定します。",
        "vi": "Bật/tắt các tool tích hợp bên dưới. Tool bị tắt sẽ bị loại khỏi bộ công cụ của agent. "
              "Connector MCP / REST-API được thiết lập ở tab con Connector."},
    "tools_admin.refresh": {"en": "Refresh", "ja": "更新", "vi": "Làm mới"},
    "tools_admin.url_fetch_group": {
        "en": "Web access (fetch_url)", "ja": "Webアクセス (fetch_url)",
        "vi": "Truy cập web (fetch_url)"},
    "tools_admin.internet_disabled": {
        "en": "Web access is OFF — enable the fetch_url tool above to allow internet access.",
        "ja": "Web アクセスはオフです — 上の fetch_url ツールを有効にするとインターネットに接続できます。",
        "vi": "Truy cập web đang TẮT — bật tool fetch_url ở trên để cho phép truy cập internet."},
    "tools_admin.subtab_tool": {"en": "Tool", "ja": "ツール", "vi": "Tool"},
    "tools_admin.subtab_connector": {"en": "Connector", "ja": "コネクター", "vi": "Connector"},
    "monitoring.tab_icons": {"en": "Icons", "ja": "アイコン", "vi": "Icon"},
    "icons_admin.title": {"en": "Icons", "ja": "アイコン", "vi": "Icon"},
    "icons_admin.hint": {
        "en": "Icons you can use for agents and flows. Type a name into a step/agent's Icon field to "
              "use it. Add your own SVG icons below — they become usable by name immediately.",
        "ja": "エージェントやフローに使えるアイコン。ステップ/エージェントのアイコン欄に名前を入力すると使えます。"
              "下から独自のSVGアイコンを追加でき、名前ですぐ使えます。",
        "vi": "Các icon dùng cho agent và flow. Gõ tên vào ô Icon của step/agent để dùng. Thêm icon SVG "
              "của bạn ở dưới — dùng được ngay bằng tên."},
    "icons_admin.search": {"en": "Search built-in icons…", "ja": "組込みアイコンを検索…", "vi": "Tìm icon có sẵn…"},
    "icons_admin.builtin": {"en": "Built-in icons", "ja": "組込みアイコン", "vi": "Icon có sẵn"},
    "icons_admin.custom": {"en": "Custom icons", "ja": "カスタムアイコン", "vi": "Icon tùy chỉnh"},
    "icons_admin.add": {"en": "Add SVG file", "ja": "SVGファイルを追加", "vi": "Thêm tệp SVG"},
    "icons_admin.paste": {"en": "Paste SVG", "ja": "SVGを貼付", "vi": "Dán SVG"},
    "icons_admin.paste_prompt": {"en": "Paste the SVG markup:", "ja": "SVGマークアップを貼り付け：",
                                 "vi": "Dán mã SVG:"},
    "icons_admin.delete": {"en": "Delete custom", "ja": "カスタムを削除", "vi": "Xóa tùy chỉnh"},
    "icons_admin.name_prompt": {"en": "Icon name (used in the Icon field)", "ja": "アイコン名（アイコン欄で使用）",
                                "vi": "Tên icon (dùng ở ô Icon)"},
    "icons_admin.select_custom": {"en": "Select a custom icon to delete.",
                                  "ja": "削除するカスタムアイコンを選択してください。",
                                  "vi": "Hãy chọn một icon tùy chỉnh để xóa."},
    "tools_admin.col_name": {"en": "Tool", "ja": "ツール", "vi": "Tool"},
    "tools_admin.col_desc": {"en": "Description", "ja": "説明", "vi": "Mô tả"},
    "tools_admin.col_enabled": {"en": "Enabled", "ja": "有効", "vi": "Bật"},
    "tools_admin.jira_note": {
        "en": "Jira connection setup moved to the Connector tab → set it up there; here you only turn "
              "the jira_search / jira_get_issue tools on or off.",
        "ja": "Jira接続の設定はConnectorタブに移動しました。設定はそちらで。ここでは jira_search / "
              "jira_get_issue ツールの有効/無効のみ切り替えます。",
        "vi": "Phần thiết lập kết nối Jira đã chuyển sang tab Connector → cài đặt ở đó; ở đây chỉ bật/tắt "
              "tool jira_search / jira_get_issue."},
    "connectors.jira_group": {"en": "Jira (read)", "ja": "Jira（読み取り）", "vi": "Jira (đọc)"},
    "connectors.jira_hint": {
        "en": "Connect once, then just paste a Jira link into Cowork or a Co4E step — the agent reads it "
              "automatically (no issue key needed). Read-only. A public Jira link works with no setup; "
              "a private one needs this connection. Create a token: id.atlassian.com → Security → API tokens.",
        "ja": "一度接続すれば、Cowork や Co4E ステップに Jira リンクを貼るだけで自動で読み取ります（課題キー不要）。"
              "読み取り専用。公開リンクは設定不要、非公開はこの接続が必要。トークン作成: id.atlassian.com → セキュリティ → APIトークン。",
        "vi": "Kết nối một lần, rồi chỉ cần dán link Jira vào Cowork hoặc bước Co4E — agent tự đọc (không cần "
              "issue key). Chỉ đọc. Link Jira công khai không cần cài đặt; link riêng tư cần kết nối này. "
              "Tạo token: id.atlassian.com → Security → API tokens."},
    "connectors.jira_paste": {"en": "Paste a link", "ja": "リンクを貼付", "vi": "Dán link"},
    "connectors.jira_paste_placeholder": {
        "en": "Paste any Jira link — fills the base URL for you",
        "ja": "Jiraのリンクを貼ると、ベースURLが自動入力されます",
        "vi": "Dán bất kỳ link Jira nào — tự điền Base URL"},
    "connectors.jira_url": {"en": "Base URL", "ja": "ベースURL", "vi": "Base URL"},
    "connectors.jira_email": {"en": "Email", "ja": "メール", "vi": "Email"},
    "connectors.jira_token": {"en": "API token", "ja": "APIトークン", "vi": "API token"},
    "connectors.jira_save": {"en": "Save", "ja": "保存", "vi": "Lưu"},
    "connectors.jira_test": {"en": "Test connection", "ja": "接続テスト", "vi": "Kiểm tra kết nối"},
    "connectors.jira_saved": {"en": "Saved.", "ja": "保存しました。", "vi": "Đã lưu."},
    "connectors.jira_connected": {"en": "connected", "ja": "接続済み", "vi": "đã kết nối"},
    "connectors.jira_not_set": {"en": "not configured", "ja": "未設定", "vi": "chưa cấu hình"},
    "connectors.jira_setup_hint": {
        "en": "Double-click to connect Jira (paste any Jira link — no per-request setup after that).",
        "ja": "ダブルクリックで Jira に接続（Jira リンクを貼るだけ、以降は設定不要）。",
        "vi": "Nhấp đúp để kết nối Jira (dán bất kỳ link Jira nào — sau đó không cần thiết lập gì thêm)."},
    "connectors.dbl_configure": {
        "en": "Double-click a connector to configure it.",
        "ja": "コネクタをダブルクリックして設定します。",
        "vi": "Nhấp đúp vào một connector để thiết lập."},
    "connectors.connect_external": {
        "en": "Connect to external connectors",
        "ja": "外部コネクタに接続する",
        "vi": "Kết nối tới connector bên ngoài"},
    "connectors.connect_external_tooltip": {
        "en": ("Master switch (default ON): when off, the agent connects to NO external "
               "connector or MCP server — the per-connector settings below are ignored."),
        "ja": "マスタースイッチ（既定オン）: オフにすると、エージェントは外部コネクタ／MCPサーバーに"
              "一切接続しません（下の個別設定は無視されます）。",
        "vi": ("Công tắc tổng (mặc định BẬT): khi tắt, agent sẽ KHÔNG kết nối tới bất kỳ connector "
               "hay MCP server bên ngoài nào — các thiết lập từng connector bên dưới bị bỏ qua.")},
    "connectors.jira_testing": {"en": "Testing…", "ja": "テスト中…", "vi": "Đang kiểm tra…"},
    "connectors.jira_ok": {"en": "✓ Connected to Jira.", "ja": "✓ Jiraに接続できました。", "vi": "✓ Kết nối Jira thành công."},
    "connectors.jira_fail": {"en": "✗ {err}", "ja": "✗ {err}", "vi": "✗ {err}"},
    "connectors.jira_need_fields": {
        "en": "Enter base URL, email and API token first.",
        "ja": "先にベースURL・メール・APIトークンを入力してください。",
        "vi": "Hãy nhập Base URL, Email và API token trước."},
    "tools_admin.jira_group": {"en": "Jira connection", "ja": "Jira 接続", "vi": "Kết nối Jira"},
    "tools_admin.jira_hint": {
        "en": "Connect once, then just paste a Jira issue link into Cowork or a Co4E step — the agent "
              "reads it automatically (no issue key needed). Read-only. Private Jira needs this one-time "
              "connection; a public Jira link works with no setup. API token: id.atlassian.com → "
              "Security → API tokens. Turn the jira tools on/off in the list above.",
        "ja": "一度接続すれば、あとは Cowork や Co4E ステップに Jira のリンクを貼るだけで自動的に読み取ります"
              "（課題キー不要）。読み取り専用。非公開Jiraはこの一度の接続が必要、公開リンクは設定不要。"
              "APIトークン: id.atlassian.com → セキュリティ → APIトークン。ツールの有効/無効は上の一覧で。",
        "vi": "Kết nối một lần, sau đó chỉ cần dán link Jira vào Cowork hoặc bước Co4E — agent tự đọc "
              "(không cần nhập issue key). Chỉ đọc. Jira riêng tư cần kết nối một lần này; link Jira công "
              "khai thì không cần cài đặt. API token: id.atlassian.com → Security → API tokens. Bật/tắt "
              "tool jira ở danh sách phía trên."},
    "tools_admin.jira_url": {"en": "Base URL", "ja": "ベースURL", "vi": "Base URL"},
    "tools_admin.jira_email": {"en": "Email", "ja": "メール", "vi": "Email"},
    "tools_admin.jira_token": {"en": "API token", "ja": "APIトークン", "vi": "API token"},
    "tools_admin.jira_save": {"en": "Save", "ja": "保存", "vi": "Lưu"},
    "tools_admin.jira_test": {"en": "Test connection", "ja": "接続テスト", "vi": "Kiểm tra kết nối"},
    "tools_admin.jira_saved": {"en": "Saved.", "ja": "保存しました。", "vi": "Đã lưu."},
    "tools_admin.jira_testing": {"en": "Testing…", "ja": "テスト中…", "vi": "Đang kiểm tra…"},
    "tools_admin.jira_ok": {"en": "✓ Connected to Jira.", "ja": "✓ Jiraに接続できました。", "vi": "✓ Kết nối Jira thành công."},
    "tools_admin.jira_fail": {"en": "✗ {err}", "ja": "✗ {err}", "vi": "✗ {err}"},
    "tools_admin.jira_need_fields": {
        "en": "Enter base URL, email and API token first.",
        "ja": "先にベースURL・メール・APIトークンを入力してください。",
        "vi": "Hãy nhập Base URL, Email và API token trước."},
    # ---- Co4E (node-graph workflow studio) --------------------------------
    "workspace.tab_co4e": {"en": "Co4E", "ja": "Co4E", "vi": "Co4E"},
    "workspace.tab_co4e_tooltip": {
        "en": "Co4E — Code for Everyone, Cowork for Everyone",
        "ja": "Co4E — Code for Everyone, Cowork for Everyone",
        "vi": "Co4E — Code for Everyone, Cowork for Everyone",
    },
    "co4e.untitled": {"en": "Untitled flow", "ja": "無題のフロー", "vi": "Flow chưa đặt tên"},
    "co4e.tab_workflows": {"en": "Workflows", "ja": "ワークフロー", "vi": "Workflows"},
    "co4e.tab_agents": {"en": "Agents", "ja": "エージェント", "vi": "Agents"},
    "co4e.tab_skills": {"en": "Skills", "ja": "スキル", "vi": "Skills"},
    "co4e.new": {"en": "New", "ja": "新規", "vi": "Mới"},
    "co4e.load": {"en": "Load", "ja": "読み込み", "vi": "Tải"},
    "co4e.delete": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "co4e.edit": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "co4e.new_agent": {"en": "New agent", "ja": "新規エージェント", "vi": "Agent mới"},
    "co4e.manage_skills": {"en": "Manage skills…", "ja": "スキル管理…", "vi": "Quản lý skill…"},
    "co4e.template": {"en": "template", "ja": "テンプレート", "vi": "mẫu"},
    "co4e.saved": {"en": "saved", "ja": "保存済み", "vi": "đã lưu"},
    "co4e.custom": {"en": "custom", "ja": "カスタム", "vi": "tùy chỉnh"},
    "co4e.parallel_node": {"en": "Parallel (fan-out)", "ja": "並列（ファンアウト）", "vi": "Song song (fan-out)"},
    "co4e.add_step": {"en": "Add step", "ja": "ステップ追加", "vi": "Thêm bước"},
    "co4e.fit": {"en": "Fit", "ja": "全体表示", "vi": "Vừa màn hình"},
    "co4e.fit_tooltip": {
        "en": "Auto-fit: zoom to show every step", "ja": "自動フィット：全ステップを表示",
        "vi": "Tự canh: thu phóng để thấy tất cả bước"},
    "co4e.drag_hint": {
        "en": "Drag a flow or agent onto the canvas (double-click a flow to load it).",
        "ja": "フローやエージェントをキャンバスにドラッグ（フローはダブルクリックで読み込み）。",
        "vi": "Kéo một flow hoặc agent vào canvas (double-click flow để tải)."},
    "co4e.blank_step": {"en": "Blank step", "ja": "空のステップ", "vi": "Bước trống"},
    "co4e.pick_agent": {"en": "Choose an agent", "ja": "エージェントを選択", "vi": "Chọn agent"},
    "co4e.ai_draft": {"en": "Draft with AI", "ja": "AIで下書き", "vi": "Soạn bằng AI"},
    "co4e.ai_draft_tooltip": {
        "en": "Let AI write this agent's instructions from its name and role (no skill needed).",
        "ja": "エージェントの名前と役割から指示文をAIが作成（スキル不要）。",
        "vi": "Để AI viết hướng dẫn cho agent từ tên và vai trò (không cần skill)."},
    "co4e.ai_draft_hint_title": {"en": "Draft with AI", "ja": "AIで下書き", "vi": "Soạn bằng AI"},
    "co4e.ai_draft_hint_label": {
        "en": "Describe what this agent should do (optional — leave blank to draft from just "
              "the name/role). More detail here → more detailed instructions.",
        "ja": "このエージェントが何をすべきか説明してください（任意 — 空欄なら名前/役割のみから"
              "下書き）。詳しく書くほど、生成される指示も詳細になります。",
        "vi": "Mô tả agent này nên làm gì (không bắt buộc — để trống sẽ soạn chỉ từ tên/vai trò). "
              "Mô tả chi tiết hơn → hướng dẫn được tạo ra chi tiết hơn."},
    "co4e.tt_add_step": {"en": "Add a blank step to the canvas", "ja": "空のステップをキャンバスに追加",
                         "vi": "Thêm một bước trống vào canvas"},
    "co4e.tt_save": {"en": "Save this flow", "ja": "このフローを保存", "vi": "Lưu flow này"},
    "co4e.tt_save_template": {"en": "Save as a reusable template", "ja": "再利用テンプレートとして保存",
                             "vi": "Lưu thành mẫu dùng lại"},
    "co4e.tt_run": {"en": "Run the flow (or Interrupt while running)", "ja": "フローを実行（実行中は中断）",
                    "vi": "Chạy flow (hoặc Dừng khi đang chạy)"},
    "co4e.tt_mode": {
        "en": "Auto = each step plans then runs · Plan = dry-run a plan (read-only) · Manual = step-by-step (advance with Next step)",
        "ja": "Auto=各ステップが計画して実行 · Plan=計画のみ（読取専用）· Manual=1ステップずつ（「次へ」で進む）",
        "vi": "Auto = mỗi bước tự lập kế hoạch rồi chạy · Plan = chỉ lập kế hoạch (chỉ đọc) · Manual = từng bước (bấm Bước tiếp)"},
    "co4e.tt_new_wf": {"en": "Start a new empty flow", "ja": "新しい空のフロー", "vi": "Tạo flow mới trống"},
    "co4e.tt_load_wf": {"en": "Load the selected flow into the canvas",
                        "ja": "選択したフローをキャンバスに読み込み", "vi": "Tải flow đã chọn vào canvas"},
    "co4e.tt_del_wf": {"en": "Delete the selected saved flow", "ja": "選択した保存フローを削除",
                       "vi": "Xóa flow đã lưu đang chọn"},
    "co4e.tt_edit_wf": {"en": "Edit the selected flow", "ja": "選択したフローを編集",
                        "vi": "Sửa flow đang chọn"},
    "co4e.tt_new_agent": {"en": "Create a custom agent persona", "ja": "カスタムエージェントを作成",
                          "vi": "Tạo một agent tùy chỉnh"},
    "co4e.tt_edit_agent": {"en": "Edit the selected custom agent", "ja": "選択したカスタムエージェントを編集",
                           "vi": "Sửa agent tùy chỉnh đang chọn"},
    "co4e.tt_del_agent": {"en": "Delete the selected custom agent", "ja": "選択したカスタムエージェントを削除",
                          "vi": "Xóa agent tùy chỉnh đang chọn"},
    "co4e.tt_manage_skills": {"en": "Open the Skills manager", "ja": "スキル管理を開く",
                              "vi": "Mở trình quản lý Skill"},
    "co4e.save": {"en": "Save", "ja": "保存", "vi": "Lưu"},
    "co4e.save_template": {"en": "Save as Template", "ja": "テンプレートとして保存", "vi": "Lưu làm mẫu"},
    "co4e.flow_name": {"en": "Flow", "ja": "フロー", "vi": "Flow"},
    "co4e.run": {"en": "Run", "ja": "実行", "vi": "Chạy"},
    "co4e.interrupt": {"en": "Interrupt", "ja": "中断", "vi": "Dừng"},
    "co4e.add": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "co4e.config_title": {"en": "Step config", "ja": "ステップ設定", "vi": "Cấu hình bước"},
    "co4e.tt_collapse_config": {"en": "Collapse the config panel", "ja": "設定パネルを折りたたむ",
                                "vi": "Thu gọn bảng cấu hình"},
    "co4e.tt_expand_config": {"en": "Expand the config panel", "ja": "設定パネルを展開",
                              "vi": "Mở rộng bảng cấu hình"},
    "co4e.messages": {"en": "Messages", "ja": "メッセージ", "vi": "Tin nhắn"},
    "co4e.tt_collapse_msgs": {"en": "Collapse the messages panel", "ja": "メッセージを折りたたむ",
                              "vi": "Thu gọn khung tin nhắn"},
    "co4e.tt_expand_msgs": {"en": "Expand the messages panel", "ja": "メッセージを展開",
                            "vi": "Mở rộng khung tin nhắn"},
    "co4e.mode.auto": {"en": "Auto", "ja": "自動", "vi": "Auto"},
    "co4e.mode.plan": {"en": "Plan", "ja": "計画", "vi": "Plan"},
    "co4e.mode.manual": {"en": "Manual", "ja": "手動", "vi": "Manual"},
    # --- Co4E run manager / duplicate / status / zoom (parallel flows) ---
    "co4e.copy_suffix": {"en": "copy", "ja": "コピー", "vi": "bản sao"},
    "co4e.tt_dup_wf": {"en": "Duplicate the selected flow (run copies in parallel)",
                       "ja": "選択フローを複製（コピーを並列実行）", "vi": "Nhân bản flow đã chọn (chạy bản sao song song)"},
    "co4e.tt_flow_name": {"en": "Flow name", "ja": "フロー名", "vi": "Tên flow"},
    "co4e.tt_add_step": {"en": "Add a step to the canvas", "ja": "キャンバスにステップを追加",
                         "vi": "Thêm một bước vào canvas"},
    "co4e.tt_zoom_in": {"en": "Zoom in (Ctrl+wheel / Ctrl++)", "ja": "拡大（Ctrl+ホイール / Ctrl++）",
                        "vi": "Phóng to (Ctrl+lăn chuột / Ctrl++)"},
    "co4e.tt_zoom_out": {"en": "Zoom out (Ctrl+wheel / Ctrl+-)", "ja": "縮小（Ctrl+ホイール / Ctrl+-）",
                         "vi": "Thu nhỏ (Ctrl+lăn chuột / Ctrl+-)"},
    "co4e.run_bg": {"en": "Run", "ja": "実行", "vi": "Chạy"},
    "co4e.tt_run_bg": {
        "en": "Run the selected flow in the background — several flows run in parallel",
        "ja": "選択フローをバックグラウンド実行 — 複数フローを並列実行",
        "vi": "Chạy flow đã chọn ở nền — nhiều flow chạy song song"},
    "co4e.running_flows": {"en": "Running flows", "ja": "実行中のフロー", "vi": "Flow đang chạy"},
    "co4e.runs_tab": {"en": "Flow Status", "ja": "フロー状態", "vi": "Flow Status"},
    "co4e.runs_tab_n": {"en": "Flow Status ({n})", "ja": "フロー状態 ({n})", "vi": "Flow Status ({n})"},
    "co4e.runs_col_flow": {"en": "Flow", "ja": "フロー", "vi": "Flow"},
    "co4e.runs_col_status": {"en": "Status", "ja": "状態", "vi": "Trạng thái"},
    "co4e.runs_col_steps": {"en": "Steps", "ja": "ステップ", "vi": "Bước"},
    "co4e.runs_col_by": {"en": "Created by", "ja": "作成者", "vi": "Người tạo"},
    "co4e.runs_col_at": {"en": "Created at", "ja": "作成日時", "vi": "Ngày tạo"},
    "co4e.tt_runs_list": {
        "en": "Live status of every running/finished flow — always up to date. Double-click a run to run that flow again.",
        "ja": "実行中／完了フローのライブ状態 — 常に最新。実行をダブルクリックでそのフローを再実行。",
        "vi": "Trạng thái trực tiếp của mọi flow đang chạy/đã xong — luôn mới nhất. Nhấp đúp để chạy lại flow đó."},
    "co4e.flow_gone": {"en": "That flow no longer exists.", "ja": "そのフローは存在しません。",
                       "vi": "Flow đó không còn tồn tại."},
    "co4e.rename": {"en": "Rename", "ja": "名前を変更", "vi": "Đổi tên"},
    "co4e.rename_prompt": {"en": "New flow name (name it by its function / task):",
                           "ja": "新しいフロー名（機能／タスクで命名）:",
                           "vi": "Tên flow mới (đặt theo chức năng / task):"},
    "co4e.renamed_msg": {"en": "Renamed to: {name}", "ja": "名前変更: {name}", "vi": "Đã đổi tên: {name}"},
    "co4e.duplicate": {"en": "Duplicate", "ja": "複製", "vi": "Nhân bản"},
    "co4e.viewing_flow": {"en": "Viewing flow: {name}", "ja": "フロー表示: {name}", "vi": "Đang xem flow: {name}"},
    "co4e.stop": {"en": "Stop", "ja": "停止", "vi": "Dừng"},
    "co4e.tt_stop_run": {"en": "Stop the selected run (or all runs if none selected)",
                         "ja": "選択した実行を停止（未選択なら全実行）", "vi": "Dừng lần chạy đã chọn (hoặc tất cả nếu chưa chọn)"},
    "co4e.clear_done": {"en": "Clear done", "ja": "完了を消去", "vi": "Xóa đã xong"},
    "co4e.tt_clear_runs": {"en": "Remove finished/stopped runs from the list",
                           "ja": "完了／停止した実行を一覧から削除", "vi": "Bỏ các lần chạy đã xong/đã dừng khỏi danh sách"},
    "co4e.select_flow": {"en": "Select a flow first.", "ja": "先にフローを選択してください。",
                         "vi": "Hãy chọn một flow trước."},
    "co4e.delete_run": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "co4e.tt_delete_run": {"en": "Delete the selected run from the history",
                           "ja": "選択した実行を履歴から削除", "vi": "Xóa lần chạy đang chọn khỏi lịch sử"},
    "co4e.open_run": {"en": "Open flow", "ja": "フローを開く", "vi": "Mở flow"},
    "co4e.open_output": {"en": "Open output folder", "ja": "出力フォルダを開く", "vi": "Mở thư mục output"},
    "co4e.open_output_link": {
        "en": "📂 Open output folder", "ja": "📂 出力フォルダを開く", "vi": "📂 Mở thư mục output"},
    "co4e.tt_open_workspace": {
        "en": "Open the workspace folder where flow outputs are saved:\n{path}",
        "ja": "フローの出力が保存されるワークスペースフォルダを開く:\n{path}",
        "vi": "Mở thư mục workspace nơi lưu output của flow:\n{path}"},
    "co4e.select_run": {"en": "Select a run first.", "ja": "先に実行を選択してください。",
                        "vi": "Hãy chọn một lần chạy trước."},
    "co4e.rename_run": {"en": "Rename", "ja": "名前変更", "vi": "Đổi tên"},
    "co4e.tt_rename_run": {"en": "Rename the selected flow run",
                           "ja": "選択した実行の名前を変更", "vi": "Đổi tên lần chạy đang chọn"},
    "co4e.rename_run_label": {"en": "New flow name:", "ja": "新しいフロー名:", "vi": "Tên flow mới:"},
    "co4e.run_done_title": {"en": "Flow finished", "ja": "フロー完了", "vi": "Flow đã xong"},
    "co4e.run_done_popup": {
        "en": "Flow \"{name}\" finished — {status}.",
        "ja": "フロー「{name}」が完了しました — {status}。",
        "vi": "Flow \"{name}\" đã chạy xong — {status}."},
    "co4e.duplicated_msg": {"en": "Duplicated: {name}", "ja": "複製しました: {name}", "vi": "Đã nhân bản: {name}"},
    "co4e.bg_started": {"en": "▶ Started in background: {name}", "ja": "▶ バックグラウンドで開始: {name}",
                        "vi": "▶ Đã chạy nền: {name}"},
    "co4e.bg_done": {"en": "Flow '{name}': {status}", "ja": "フロー '{name}': {status}",
                     "vi": "Flow '{name}': {status}"},
    "co4e.tool_failed": {"en": "⚠ tool failed: {name}", "ja": "⚠ ツール失敗: {name}", "vi": "⚠ tool lỗi: {name}"},
    "co4e.manual_started": {"en": "▶ Manual run: {name} — advance with Run/Next step.",
                            "ja": "▶ 手動実行: {name} — 「実行/次へ」で進む。",
                            "vi": "▶ Chạy thủ công: {name} — bấm Chạy/Bước tiếp để tiến."},
    "co4e.manual_step": {"en": "▶ Step {i}/{n}: {label}", "ja": "▶ ステップ {i}/{n}: {label}",
                         "vi": "▶ Bước {i}/{n}: {label}"},
    "co4e.status.running": {"en": "running", "ja": "実行中", "vi": "đang chạy"},
    "co4e.status.done": {"en": "done", "ja": "完了", "vi": "xong"},
    "co4e.status.error": {"en": "error", "ja": "エラー", "vi": "lỗi"},
    "co4e.status.stopped": {"en": "stopped", "ja": "停止", "vi": "đã dừng"},
    "co4e.chat_placeholder": {
        "en": "Chat with the flow — use /agent:<name> or /skill:<name>",
        "ja": "フローとチャット — /agent:<name> または /skill:<name>",
        "vi": "Chat với flow — dùng /agent:<name> hoặc /skill:<name>"},
    "co4e.send": {"en": "Send", "ja": "送信", "vi": "Gửi"},
    "co4e.f_label": {"en": "Label", "ja": "ラベル", "vi": "Nhãn"},
    "co4e.f_role": {"en": "Role", "ja": "ロール", "vi": "Vai trò"},
    "co4e.f_name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "co4e.f_icon": {"en": "Icon", "ja": "アイコン", "vi": "Icon"},
    "co4e.f_icon_placeholder": {"en": "icon name (optional)", "ja": "アイコン名（任意）", "vi": "tên icon (tùy chọn)"},
    "co4e.f_instructions": {"en": "Instructions", "ja": "指示", "vi": "Hướng dẫn"},
    "co4e.f_context": {"en": "Context", "ja": "コンテキスト", "vi": "Ngữ cảnh"},
    "co4e.f_context_placeholder": {
        "en": "Extra background/info for this agent or step (added to its prompt at run time).",
        "ja": "このエージェント/ステップ用の追加情報（実行時にプロンプトへ追加されます）。",
        "vi": "Thông tin/ngữ cảnh bổ sung cho agent hoặc step này (thêm vào prompt khi chạy)."},
    "co4e.f_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "co4e.f_permission": {"en": "Permissions", "ja": "権限", "vi": "Quyền"},
    "co4e.f_self_verify": {"en": "Self-verify", "ja": "自己検証", "vi": "Tự kiểm tra"},
    "co4e.f_verify_rounds": {"en": "rounds", "ja": "回数", "vi": "vòng"},
    "co4e.f_skills": {"en": "Skills", "ja": "スキル", "vi": "Skills"},
    "co4e.f_attachments": {"en": "Attachments", "ja": "添付ファイル", "vi": "Tệp đính kèm"},
    "co4e.attach_add": {"en": "Attach files", "ja": "ファイル添付", "vi": "Đính kèm tệp"},
    "co4e.attach_remove": {"en": "Remove", "ja": "削除", "vi": "Bỏ"},
    "co4e.f_subagents": {"en": "Parallel agents", "ja": "並列エージェント", "vi": "Agent song song"},
    "co4e.perm.inherit": {"en": "Inherit", "ja": "継承", "vi": "Kế thừa"},
    "co4e.perm.read-only": {"en": "Read-only", "ja": "読み取り専用", "vi": "Chỉ đọc"},
    "co4e.perm.standard": {"en": "Standard", "ja": "標準", "vi": "Tiêu chuẩn"},
    "co4e.perm.full": {"en": "Full", "ja": "フル", "vi": "Toàn quyền"},
    "co4e.add_subagent": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "co4e.del_subagent": {"en": "Remove", "ja": "削除", "vi": "Bỏ"},
    "co4e.run_this_step": {"en": "Run this step", "ja": "このステップを実行", "vi": "Chạy bước này"},
    "co4e.run_from_here": {"en": "Run from here", "ja": "ここから実行", "vi": "Chạy từ đây"},
    "co4e.delete_step": {"en": "Delete step", "ja": "ステップ削除", "vi": "Xóa bước"},
    "co4e.load_models_tooltip": {
        "en": "Load available models", "ja": "利用可能なモデルを取得", "vi": "Tải danh sách model"},
    "co4e.agent_edit_title": {"en": "Edit agent", "ja": "エージェント編集", "vi": "Sửa agent"},
    "co4e.agent_new_title": {"en": "New agent", "ja": "新規エージェント", "vi": "Agent mới"},
    "co4e.saved_msg": {"en": "Saved flow: {name}", "ja": "フローを保存: {name}", "vi": "Đã lưu flow: {name}"},
    "co4e.select_custom_agent": {
        "en": "Select a custom agent first.", "ja": "先にカスタムエージェントを選択してください。",
        "vi": "Hãy chọn một agent tùy chỉnh trước."},
    "co4e.no_steps": {"en": "Add at least one step first.", "ja": "先にステップを追加してください。",
                      "vi": "Hãy thêm ít nhất một bước."},
    "co4e.run_started": {"en": "▶ Running flow: {name}", "ja": "▶ フロー実行中: {name}",
                         "vi": "▶ Đang chạy flow: {name}"},
    "co4e.run_done": {"en": "✓ Flow finished.", "ja": "✓ フロー完了。", "vi": "✓ Flow xong."},
    "co4e.run_execute_phase": {
        "en": "▶ Plan done — now executing…", "ja": "▶ 計画完了 — 実行中…",
        "vi": "▶ Xong plan — đang thực thi…"},
    "co4e.agent_not_found": {
        "en": "Agent '{name}' not found.", "ja": "エージェント '{name}' が見つかりません。",
        "vi": "Không tìm thấy agent '{name}'."},

    # ---- agents_admin_tab.py — Admin-only agent catalog -------------------
    "agents_admin.hint": {
        "en": "System-management agents shared across every machine (stored in the shared accounts folder): the help agent and Schedule Task executors. These are NOT the agents you pick in Cowork or Co4E.",
        "ja": "全マシンで共有されるシステム管理用エージェント（共有フォルダーに保存）：ヘルプエージェントやスケジュールタスクの実行エージェントなど。CoworkやCo4Eで選択するエージェントではありません。",
        "vi": "Agent quản lý hệ thống, dùng chung mọi máy (lưu trong thư mục dùng chung): agent trợ giúp và agent chạy Schedule Task. Đây KHÔNG phải agent để chọn trong Cowork hay Co4E."},
    "agents_admin.add_title": {"en": "Add agent", "ja": "エージェント追加", "vi": "Thêm agent"},
    "agents_admin.edit_title": {"en": "Edit agent", "ja": "エージェント編集", "vi": "Sửa agent"},
    "agents_admin.delete_title": {"en": "Delete agent", "ja": "エージェント削除", "vi": "Xóa agent"},
    "agents_admin.delete_confirm": {
        "en": "Delete agent \"{name}\"?", "ja": "エージェント「{name}」を削除しますか？",
        "vi": "Xóa agent \"{name}\"?"},
    "agents_admin.add_btn": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "agents_admin.edit_btn": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "agents_admin.delete_btn": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "agents_admin.f_name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "agents_admin.f_kind": {"en": "App function", "ja": "アプリ機能", "vi": "Chức năng App"},
    "agents_admin.f_prompt": {"en": "Instructions", "ja": "指示", "vi": "Chỉ dẫn"},
    "agents_admin.f_prompt_placeholder": {
        "en": "Extra instructions this agent always follows (optional)…",
        "ja": "このエージェントが常に従う追加指示（任意）…",
        "vi": "Chỉ dẫn bổ sung agent này luôn tuân theo (tùy chọn)…"},
    "agents_admin.f_provider": {"en": "Provider", "ja": "プロバイダー", "vi": "Provider"},
    "agents_admin.provider_default": {
        "en": "(machine's active provider)", "ja": "（各マシンの現在のプロバイダー）",
        "vi": "(provider hiện tại của máy)"},
    "agents_admin.f_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "agents_admin.f_model_placeholder": {
        "en": "empty = each machine's Settings model (currently: {model})",
        "ja": "空欄 = 各マシンの設定モデル（現在: {model}）",
        "vi": "để trống = model trong Settings của từng máy (hiện tại: {model})"},
    "agents_admin.load_models_tooltip": {
        "en": "Fetch this provider's real model list so you can pick a specific one from the dropdown.",
        "ja": "このプロバイダーの実際のモデル一覧を取得し、ドロップダウンから選択できるようにします。",
        "vi": "Lấy danh sách model thực tế của provider này để chọn từ dropdown."},
    "agents_admin.load_models_empty": {
        "en": "No models were returned — check the provider's settings/connection.",
        "ja": "モデルが取得できませんでした。プロバイダーの設定/接続を確認してください。",
        "vi": "Không lấy được model nào — kiểm tra lại cấu hình/kết nối provider."},
    "agents_admin.f_enabled": {"en": "Enabled", "ja": "有効", "vi": "Kích hoạt"},
    "agents_admin.col_name": {"en": "Name", "ja": "名前", "vi": "Tên"},
    "agents_admin.col_kind": {"en": "Function", "ja": "機能", "vi": "Chức năng"},
    "agents_admin.col_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "agents_admin.col_enabled": {"en": "Enabled", "ja": "有効", "vi": "Kích hoạt"},
    "agents_admin.col_status": {"en": "Status", "ja": "状態", "vi": "Trạng thái"},
    "agents_admin.col_updated": {"en": "Updated", "ja": "更新", "vi": "Cập nhật"},
    "agents_admin.check_btn": {"en": "Check", "ja": "チェック", "vi": "Kiểm tra"},
    "agents_admin.check_tooltip": {
        "en": "Check each agent's effective provider/model connectivity",
        "ja": "各エージェントの実効プロバイダ/モデルの接続性を確認",
        "vi": "Kiểm tra kết nối provider/model hiệu lực của từng agent"},
    "agents_admin.status_unchecked": {"en": "— (not checked)", "ja": "— (未チェック)", "vi": "— (chưa kiểm tra)"},
    "agents_admin.status_unchecked_tip": {
        "en": "Press Check to test whether this agent's provider/model is reachable",
        "ja": "「チェック」でこのエージェントのプロバイダ/モデルへの到達性をテスト",
        "vi": "Nhấn Kiểm tra để test agent này có kết nối được provider/model không"},
    "agents_admin.status_checking": {"en": "checking…", "ja": "確認中…", "vi": "đang kiểm tra…"},
    "agents_admin.status_ok": {"en": "Active", "ja": "稼働中", "vi": "Hoạt động"},
    "agents_admin.status_bad": {"en": "Error", "ja": "エラー", "vi": "Lỗi"},
    "agents_admin.default_model": {
        "en": "(Settings default: {model})", "ja": "（設定既定: {model}）",
        "vi": "(mặc định Settings: {model})"},
    "agents_admin.kind.search": {"en": "Search", "ja": "検索", "vi": "Tìm kiếm"},
    "agents_admin.kind.monitor": {"en": "Monitoring", "ja": "監視", "vi": "Giám sát"},
    "agents_admin.kind.cowork": {"en": "Cowork chat", "ja": "Cowork チャット", "vi": "Cowork chat"},
    "agents_admin.kind.graphrag": {"en": "GraphRAG / Knowledge", "ja": "GraphRAG / ナレッジ", "vi": "GraphRAG / Tri thức"},
    "agents_admin.kind.schedule": {"en": "Schedule Task", "ja": "スケジュールタスク", "vi": "Schedule Task"},
    "agents_admin.kind.security": {"en": "Security", "ja": "セキュリティ", "vi": "Bảo mật"},
    "agents_admin.kind.help": {"en": "App Help", "ja": "アプリヘルプ", "vi": "Trợ giúp App"},

    "monitoring.filter_placeholder": {
        "en": "Filter rows (or type a question and press )…",
        "ja": "行をフィルター（質問を入力しても可）…",
        "vi": "Lọc dòng (hoặc gõ câu hỏi rồi bấm )…"},
    "monitoring.ai_filter_btn": {"en": "AI", "ja": "AI", "vi": "AI"},
    "monitoring.pricing_title": {
        "en": "Model pricing (USD / 1M tokens)", "ja": "モデル価格表 (USD / 100万トークン)",
        "vi": "Bảng giá model (USD / 1 triệu token)"},
    "monitoring.pricing_col_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "monitoring.pricing_col_in": {"en": "In", "ja": "入力", "vi": "In"},
    "monitoring.pricing_col_out": {"en": "Out", "ja": "出力", "vi": "Out"},
    "monitoring.pricing_col_cache": {"en": "Cache", "ja": "キャッシュ", "vi": "Cache"},
    "monitoring.pricing_add_btn": {"en": "Add model", "ja": "モデル追加", "vi": "Thêm model"},
    "monitoring.pricing_del_btn": {"en": "Remove", "ja": "削除", "vi": "Xóa"},
    "monitoring.pricing_link_label": {
        "en": "Reference:", "ja": "参考リンク:", "vi": "Link tham khảo:"},
    "monitoring.pricing_no_link": {
        "en": "No reference link set (Settings → Parameter → Pricing reference link).",
        "ja": "参考リンク未設定（設定 → Parameter）。",
        "vi": "Chưa đặt link tham khảo (Settings → Parameter → Link bảng giá)."},
    "monitoring.ai_filter_tooltip": {
        "en": "AI turns your question into a filter keyword (e.g. \"which commands failed today?\").",
        "ja": "質問をAIがフィルターキーワードに変換します。",
        "vi": "AI chuyển câu hỏi thành từ khóa lọc (vd: \"hôm nay lệnh nào bị lỗi?\")."},
    "monitoring.overview_activity_title": {
        "en": "Recent Activity", "ja": "最近のアクティビティ", "vi": "Hoạt động gần đây"},
    "monitoring.overview_no_activity": {
        "en": "No activity yet.", "ja": "まだアクティビティはありません。", "vi": "Chưa có hoạt động nào."},
    "monitoring.overview_resource_title": {
        "en": "Resource Usage", "ja": "リソース使用状況", "vi": "Sử dụng tài nguyên"},
    "monitoring.overview_res_cpu": {"en": "CPU", "ja": "CPU", "vi": "CPU"},
    "monitoring.overview_res_mem": {"en": "Memory", "ja": "メモリ", "vi": "Bộ nhớ"},
    "monitoring.overview_res_disk": {"en": "Disk I/O", "ja": "ディスク I/O", "vi": "Disk I/O"},
    "monitoring.overview_res_network": {"en": "Network", "ja": "ネットワーク", "vi": "Mạng"},
    # ---- model pricing list (Overview, beside the resource group) ----
    "monitoring.pricing_title": {"en": "Model pricing", "ja": "モデル料金", "vi": "Bảng giá model"},
    "monitoring.pricing_currency": {"en": "Currency", "ja": "通貨", "vi": "Tiền tệ"},
    "monitoring.pricing_import": {"en": "Import", "ja": "取込", "vi": "Nhập"},
    "monitoring.pricing_export": {"en": "Template", "ja": "テンプレート", "vi": "Mẫu"},
    "monitoring.pricing_add": {"en": "Add", "ja": "追加", "vi": "Thêm"},
    "monitoring.pricing_autolink": {"en": "Auto-link", "ja": "自動取得", "vi": "Tự lấy"},
    "monitoring.pricing_delete": {"en": "Delete", "ja": "削除", "vi": "Xóa"},
    "monitoring.pricing_add_prompt": {"en": "Model name:", "ja": "モデル名:", "vi": "Tên model:"},
    "monitoring.pricing_imported": {"en": "Imported {n} model prices.", "ja": "{n} 件の料金を取込。",
                                    "vi": "Đã nhập {n} dòng giá."},
    "monitoring.pricing_exported": {"en": "Price template exported.", "ja": "料金テンプレートを出力。",
                                    "vi": "Đã xuất mẫu bảng giá."},
    "monitoring.pricing_linked": {"en": "Linked {n} models from providers.",
                                  "ja": "プロバイダから {n} モデルを取得。",
                                  "vi": "Đã lấy {n} model từ provider."},
    "monitoring.pricing_col_model": {"en": "Model", "ja": "モデル", "vi": "Model"},
    "monitoring.pricing_col_context": {"en": "Context", "ja": "コンテキスト", "vi": "Context"},
    "monitoring.pricing_col_maxout": {"en": "Max output", "ja": "最大出力", "vi": "Max output"},
    "monitoring.pricing_col_input": {"en": "Input price", "ja": "入力単価", "vi": "Giá input"},
    "monitoring.pricing_col_output": {"en": "Output price", "ja": "出力単価", "vi": "Giá output"},
    "monitoring.overview_sandbox_details_title": {
        "en": "Sandbox Details", "ja": "サンドボックス詳細", "vi": "Chi tiết Sandbox"},
    "monitoring.overview_sandbox_id": {"en": "Sandbox ID", "ja": "サンドボックス ID", "vi": "Sandbox ID"},
    "monitoring.overview_status": {"en": "Status", "ja": "状態", "vi": "Trạng thái"},
    "monitoring.overview_status_running": {"en": "Running", "ja": "実行中", "vi": "Đang chạy"},
    "monitoring.overview_created": {"en": "Created", "ja": "作成日時", "vi": "Tạo lúc"},
    "monitoring.overview_uptime": {"en": "Uptime", "ja": "稼働時間", "vi": "Thời gian hoạt động"},
    "monitoring.overview_resource_limits": {
        "en": "Resource Limits", "ja": "リソース制限", "vi": "Giới hạn tài nguyên"},
    "monitoring.overview_edit": {"en": "Edit", "ja": "編集", "vi": "Sửa"},
    "monitoring.overview_network_label": {"en": "Network", "ja": "ネットワーク", "vi": "Mạng"},
    "monitoring.overview_network_disabled": {"en": "Disabled", "ja": "無効", "vi": "Đã tắt"},
    "monitoring.overview_network_enabled": {"en": "Enabled", "ja": "有効", "vi": "Đang mở"},
    "monitoring.overview_permissions_title": {"en": "Permissions", "ja": "権限", "vi": "Quyền"},
    "monitoring.overview_perm_fs": {"en": "File System", "ja": "ファイルシステム", "vi": "Hệ thống file"},
    "monitoring.overview_perm_fs_value": {"en": "Read/Write", "ja": "読み書き", "vi": "Đọc/Ghi"},
    "monitoring.overview_perm_network": {"en": "Network", "ja": "ネットワーク", "vi": "Mạng"},
    "monitoring.overview_perm_network_blocked": {"en": "Blocked", "ja": "ブロック", "vi": "Bị chặn"},
    "monitoring.overview_perm_network_allowed": {"en": "Allowed", "ja": "許可", "vi": "Cho phép"},
    "monitoring.overview_perm_process": {"en": "Process", "ja": "プロセス", "vi": "Tiến trình"},
    "monitoring.overview_perm_process_value": {"en": "Limited", "ja": "制限あり", "vi": "Bị hạn chế"},
    "monitoring.overview_perm_env": {"en": "Environment", "ja": "実行環境", "vi": "Môi trường"},
    "monitoring.overview_perm_env_value": {"en": "Restricted", "ja": "制限あり", "vi": "Bị giới hạn"},
    "monitoring.overview_audit_title": {"en": "Audit Log", "ja": "監査ログ", "vi": "Audit Log"},
    "monitoring.overview_view_all": {"en": "View all", "ja": "すべて表示", "vi": "Xem tất cả"},
    "monitoring.time_just_now": {"en": "just now", "ja": "たった今", "vi": "vừa xong"},
    "monitoring.time_minutes_ago": {"en": "{n}m ago", "ja": "{n}分前", "vi": "{n} phút trước"},
    "monitoring.time_hours_ago": {"en": "{n}h ago", "ja": "{n}時間前", "vi": "{n} giờ trước"},
    "monitoring.time_days_ago": {"en": "{n}d ago", "ja": "{n}日前", "vi": "{n} ngày trước"},
    "monitoring.na": {"en": "—", "ja": "—", "vi": "—"},
}


def set_language(lang: str) -> None:
    """Switch the active language and notify every registered persistent widget."""
    global _current
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE
    if lang == _current:
        return
    _current = lang
    for fn in list(_listeners):
        try:
            fn()
        except RuntimeError:
            # The widget behind this callback was already destroyed — drop it.
            try:
                _listeners.remove(fn)
            except ValueError:
                pass


def get_language() -> str:
    return _current


def tr(key: str, **kwargs) -> str:
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_current) or entry.get("en") or next(iter(entry.values()), key)
    return text.format(**kwargs) if kwargs else text


def on_language_changed(fn: Callable[[], None]) -> None:
    """Register a callback that re-applies translations to a persistent widget.

    Called once immediately (to apply the current language) and again on every
    future call to :func:`set_language`."""
    _listeners.append(fn)
    fn()
