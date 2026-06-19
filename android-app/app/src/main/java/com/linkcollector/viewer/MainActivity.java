package com.linkcollector.viewer;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Build;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.webkit.HttpAuthHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.text.InputType;
import android.text.Editable;
import android.text.TextWatcher;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "link_collector_auth";
    private static final String KEY_BASE_URL = "base_url";
    private static final String KEY_USERNAME = "username";
    private static final String KEY_PASSWORD = "password";
    private static final String DEFAULT_BASE_URL = "https://your-server.example.com";
    private static final String DEFAULT_USERNAME = "your-username";
    private int currentVersionCode() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    private WebView webView;
    private ProgressBar progressBar;
    private View loginView;
    private EditText serverInput;
    private EditText usernameInput;
    private EditText passwordInput;
    private TextView loginStatus;
    private Button loginButton;
    private SharedPreferences preferences;
    private String baseUrl;
    private String username;
    private String password;
    private String pendingSharedText;
    private boolean forceUpdateRequired;
    private UpdateInfo pendingUpdateInfo;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        pendingSharedText = extractSharedText(getIntent());
        buildUi();
        configureWebView();
        if (loadSavedCredentials()) {
            checkForUpdateThenShowBrowser();
        } else {
            checkForUpdate(baseUrl, null);
            showLogin();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String sharedText = extractSharedText(intent);
        if (sharedText != null && !sharedText.trim().isEmpty()) {
            collectSharedText(sharedText);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.GONE);

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                8,
                Gravity.TOP
        );
        root.addView(progressBar, progressParams);
        loginView = createLoginView();
        root.addView(loginView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        setContentView(root);
    }

    private View createLoginView() {
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(0xFFFFFFFF);

        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setGravity(Gravity.CENTER_HORIZONTAL);
        int horizontal = dp(34);
        container.setPadding(horizontal, dp(22), horizontal, dp(26));
        scrollView.addView(container, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT,
                ScrollView.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout topRow = new LinearLayout(this);
        topRow.setOrientation(LinearLayout.HORIZONTAL);
        topRow.setGravity(Gravity.CENTER_VERTICAL);
        container.addView(topRow, matchWidth(dp(64)));

        TextView back = new TextView(this);
        back.setText("‹");
        back.setTextSize(44);
        back.setTextColor(0xFF333333);
        back.setGravity(Gravity.CENTER_VERTICAL);
        topRow.addView(back, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        TextView mode = new TextView(this);
        mode.setText("密码登录");
        mode.setTextSize(18);
        mode.setTextColor(0xFF9B9B9B);
        mode.setGravity(Gravity.CENTER_VERTICAL | Gravity.RIGHT);
        topRow.addView(mode, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        TextView title = new TextView(this);
        title.setText("登录后更精彩");
        title.setTextSize(31);
        title.setTextColor(0xFF2C2C2C);
        title.setGravity(Gravity.CENTER);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams titleParams = matchWidth(dp(96));
        titleParams.setMargins(0, dp(46), 0, dp(42));
        container.addView(title, titleParams);

        serverInput = createInput("请输入服务器地址", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        serverInput.setText(preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL));
        container.addView(fieldBlock("服务器", serverInput), matchWidth(dp(64)));

        usernameInput = createInput("请输入账号", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        usernameInput.setText(preferences.getString(KEY_USERNAME, DEFAULT_USERNAME));
        container.addView(fieldBlock("账号", usernameInput), matchWidth(dp(64)));

        passwordInput = createInput("请输入密码", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        container.addView(fieldBlock("密码", passwordInput), matchWidth(dp(64)));

        LinearLayout agreement = new LinearLayout(this);
        agreement.setOrientation(LinearLayout.HORIZONTAL);
        agreement.setGravity(Gravity.TOP);
        LinearLayout.LayoutParams agreementParams = matchWidth(dp(54));
        agreementParams.setMargins(0, dp(14), 0, dp(10));
        container.addView(agreement, agreementParams);

        TextView circle = new TextView(this);
        circle.setText("○");
        circle.setTextSize(20);
        circle.setTextColor(0xFF8A8A8A);
        circle.setGravity(Gravity.CENTER);
        agreement.addView(circle, new LinearLayout.LayoutParams(dp(24), dp(32)));

        TextView agreementText = new TextView(this);
        agreementText.setText("我已阅读并同意《用户协议》《隐私政策》");
        agreementText.setTextSize(14);
        agreementText.setTextColor(0xFF8A8A8A);
        agreementText.setLineSpacing(2, 1.0f);
        agreement.addView(agreementText, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        loginButton = new Button(this);
        loginButton.setText("登录");
        loginButton.setAllCaps(false);
        loginButton.setTextSize(18);
        loginButton.setTextColor(0xFFFFFFFF);
        loginButton.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        loginButton.setOnClickListener(view -> login());
        LinearLayout.LayoutParams buttonParams = matchWidth(dp(50));
        buttonParams.setMargins(0, dp(16), 0, 0);
        container.addView(loginButton, buttonParams);

        loginStatus = new TextView(this);
        loginStatus.setTextSize(13);
        loginStatus.setTextColor(0xFF8A8A8A);
        loginStatus.setGravity(Gravity.CENTER);
        loginStatus.setPadding(0, dp(18), 0, 0);
        container.addView(loginStatus, matchWidth(dp(72)));

        TextView divider = new TextView(this);
        divider.setText("—  其他方式登录  —");
        divider.setTextSize(14);
        divider.setTextColor(0xFFD0D0D0);
        divider.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams dividerParams = matchWidth(dp(44));
        dividerParams.setMargins(0, dp(144), 0, dp(16));
        container.addView(divider, dividerParams);

        LinearLayout socialRow = new LinearLayout(this);
        socialRow.setOrientation(LinearLayout.HORIZONTAL);
        socialRow.setGravity(Gravity.CENTER);
        container.addView(socialRow, matchWidth(dp(72)));

        addSocialButton(socialRow, "微", 0xFF36C263);
        addSocialButton(socialRow, "QQ", 0xFF111111);
        addSocialButton(socialRow, "博", 0xFFE64235);
        addSocialButton(socialRow, "", 0xFF111111);

        TextWatcher watcher = new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {
            }

            @Override
            public void onTextChanged(CharSequence s, int start, int before, int count) {
                updateLoginButtonState();
            }

            @Override
            public void afterTextChanged(Editable s) {
            }
        };
        serverInput.addTextChangedListener(watcher);
        usernameInput.addTextChangedListener(watcher);
        passwordInput.addTextChangedListener(watcher);
        updateLoginButtonState();

        return scrollView;
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                progressBar.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                progressBar.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progressBar.setVisibility(View.GONE);
                if (pendingSharedText != null && !pendingSharedText.trim().isEmpty()) {
                    String text = pendingSharedText;
                    pendingSharedText = null;
                    collectSharedText(text);
                }
            }

            @Override
            public void onReceivedHttpAuthRequest(
                    WebView view,
                    HttpAuthHandler handler,
                    String host,
                    String realm
            ) {
                if (credentialsMatchHost(host)) {
                    handler.proceed(username, password);
                    return;
                }
                handler.cancel();
            }
        });
    }

    private String extractSharedText(Intent intent) {
        if (intent == null) {
            return null;
        }
        if (!Intent.ACTION_SEND.equals(intent.getAction())) {
            return null;
        }
        CharSequence value = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        return value == null ? null : value.toString();
    }

    private boolean loadSavedCredentials() {
        String savedPassword = preferences.getString(KEY_PASSWORD, "");
        if (savedPassword == null || savedPassword.trim().isEmpty()) {
            baseUrl = normalizeBaseUrl(preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL));
            username = preferences.getString(KEY_USERNAME, DEFAULT_USERNAME);
            password = "";
            return false;
        }
        baseUrl = normalizeBaseUrl(preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL));
        username = preferences.getString(KEY_USERNAME, DEFAULT_USERNAME);
        password = savedPassword;
        return true;
    }

    private void showLogin() {
        loginView.setVisibility(View.VISIBLE);
        webView.setVisibility(View.GONE);
        progressBar.setVisibility(View.GONE);
        loginButton.setEnabled(true);
        updateLoginButtonState();
    }

    private void showBrowser() {
        loginView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(viewerUrl());
    }

    private void checkForUpdateThenShowBrowser() {
        new Thread(() -> {
            UpdateInfo updateInfo = fetchUpdateInfo(baseUrl);
            if (updateInfo != null && updateInfo.forceUpdate) {
                runOnUiThread(() -> showForceUpdateDialog(updateInfo));
                return;
            }
            runOnUiThread(this::showBrowser);
        }).start();
    }

    private void checkForUpdate(String targetBaseUrl, Runnable onAllowed) {
        new Thread(() -> {
            UpdateInfo updateInfo = fetchUpdateInfo(targetBaseUrl);
            if (updateInfo != null && updateInfo.forceUpdate) {
                runOnUiThread(() -> showForceUpdateDialog(updateInfo));
                return;
            }
            if (onAllowed != null) {
                runOnUiThread(onAllowed);
            }
        }).start();
    }

    private void login() {
        String nextBaseUrl = normalizeBaseUrl(serverInput.getText().toString());
        String nextUsername = usernameInput.getText().toString().trim();
        String nextPassword = passwordInput.getText().toString();
        if (nextBaseUrl.isEmpty() || nextUsername.isEmpty() || nextPassword.isEmpty()) {
            setLoginStatus("请填写服务器地址、账号和密码。", true);
            return;
        }

        loginButton.setEnabled(false);
        setLoginStatus("正在验证登录信息…", false);
        new Thread(() -> {
            try {
                boolean ok = checkHealth(nextBaseUrl, nextUsername, nextPassword);
                if (!ok) {
                    runOnUiThread(() -> {
                        loginButton.setEnabled(true);
                        setLoginStatus("登录失败，请检查账号、密码或服务器地址。", true);
                    });
                    return;
                }
                baseUrl = nextBaseUrl;
                username = nextUsername;
                password = nextPassword;
                preferences.edit()
                        .putString(KEY_BASE_URL, baseUrl)
                        .putString(KEY_USERNAME, username)
                        .putString(KEY_PASSWORD, password)
                        .apply();
                runOnUiThread(() -> {
                    Toast.makeText(this, "登录成功", Toast.LENGTH_SHORT).show();
                    checkForUpdateThenShowBrowser();
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    loginButton.setEnabled(true);
                    setLoginStatus(exception.getMessage() == null ? "登录失败。" : exception.getMessage(), true);
                });
            }
        }).start();
    }

    private void collectSharedText(String text) {
        if (password == null || password.trim().isEmpty()) {
            pendingSharedText = text;
            showLogin();
            return;
        }
        Toast.makeText(this, "正在采集分享链接", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("source", text);
                payload.put("platform", "auto");
                payload.put("downloadMedia", true);
                JSONObject response = postJson(baseUrl + "/api/collect", payload);
                if (response.optBoolean("ok")) {
                    runOnUiThread(() -> {
                        Toast.makeText(this, "采集完成", Toast.LENGTH_SHORT).show();
                        webView.loadUrl(viewerUrl());
                    });
                } else {
                    String error = response.optString("error", "采集失败");
                    showError(error);
                }
            } catch (Exception exception) {
                showError(exception.getMessage() == null ? "采集失败" : exception.getMessage());
            }
        }).start();
    }

    private JSONObject postJson(String target, JSONObject payload) throws Exception {
        URL url = new URL(target);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(300000);
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setRequestProperty("Authorization", basicAuth(username, password));

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        connection.setFixedLengthStreamingMode(body.length);
        try (OutputStream output = connection.getOutputStream()) {
            output.write(body);
        }

        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300
                ? connection.getInputStream()
                : connection.getErrorStream();
        String text = readAll(stream);
        if (text == null || text.isEmpty()) {
            JSONObject response = new JSONObject();
            response.put("ok", false);
            response.put("error", "服务器返回空响应");
            return response;
        }
        return new JSONObject(text);
    }

    private boolean checkHealth(String targetBaseUrl, String targetUsername, String targetPassword) throws Exception {
        URL url = new URL(targetBaseUrl + "/healthz");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(15000);
        connection.setRequestMethod("GET");
        connection.setRequestProperty("Authorization", basicAuth(targetUsername, targetPassword));
        return connection.getResponseCode() == 200;
    }

    private UpdateInfo fetchUpdateInfo(String targetBaseUrl) {
        try {
            URL url = new URL(normalizeBaseUrl(targetBaseUrl) + "/api/app-version");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(8000);
            connection.setRequestMethod("GET");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                return null;
            }
            JSONObject response = new JSONObject(readAll(connection.getInputStream()));
            int minSupportedVersionCode = response.optInt("minSupportedVersionCode", 1);
            int latestVersionCode = response.optInt("latestVersionCode", minSupportedVersionCode);
            int installedVersionCode = currentVersionCode();
            boolean forceUpdate = response.optBoolean("forceUpdate", true)
                    && (installedVersionCode < minSupportedVersionCode || installedVersionCode < latestVersionCode);
            UpdateInfo info = new UpdateInfo();
            info.forceUpdate = forceUpdate;
            info.latestVersionName = response.optString("latestVersionName", "最新版本");
            info.latestVersionCode = latestVersionCode;
            info.downloadUrl = response.optString("apkUrl", response.optString("downloadUrl", normalizeBaseUrl(targetBaseUrl) + "/downloads/social-media-claw-debug.apk"));
            info.title = response.optString("title", "发现新版本");
            info.message = response.optString("message", "当前版本需要更新后继续使用。");
            return info;
        } catch (Exception exception) {
            return null;
        }
    }

    private void showForceUpdateDialog(UpdateInfo updateInfo) {
        forceUpdateRequired = true;
        pendingUpdateInfo = updateInfo;
        loginView.setVisibility(View.GONE);
        webView.setVisibility(View.GONE);
        progressBar.setVisibility(View.GONE);
        new AlertDialog.Builder(this)
                .setTitle(updateInfo.title)
                .setMessage(updateInfo.message + "\n\n最新版本：" + updateInfo.latestVersionName)
                .setCancelable(false)
                .setPositiveButton("立即更新", (dialog, which) -> downloadApkInApp(updateInfo))
                .show();
    }

    private void downloadApkInApp(UpdateInfo updateInfo) {
        try {
            Toast.makeText(this, "开始下载更新包…", Toast.LENGTH_SHORT).show();
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(updateInfo.downloadUrl));
            request.setTitle("社媒 claw 更新包");
            request.setDescription("下载完成后会打开安装页面");
            request.setMimeType("application/vnd.android.package-archive");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, "social-media-claw-update.apk");
            long downloadId = manager.enqueue(request);
            BroadcastReceiver receiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context context, Intent intent) {
                    long completedId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
                    if (completedId != downloadId) {
                        return;
                    }
                    try {
                        unregisterReceiver(this);
                    } catch (Exception ignored) {
                    }
                    Uri apkUri = manager.getUriForDownloadedFile(downloadId);
                    if (apkUri == null) {
                        Toast.makeText(MainActivity.this, "更新包下载失败", Toast.LENGTH_LONG).show();
                        showForceUpdateDialog(updateInfo);
                        return;
                    }
                    openApkInstaller(apkUri);
                }
            };
            IntentFilter filter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(receiver, filter);
            }
        } catch (Exception exception) {
            Toast.makeText(this, "下载更新失败：" + exception.getMessage(), Toast.LENGTH_LONG).show();
            showForceUpdateDialog(updateInfo);
        }
    }

    private void openApkInstaller(Uri apkUri) {
        try {
            Intent installIntent = new Intent(Intent.ACTION_VIEW);
            installIntent.setDataAndType(apkUri, "application/vnd.android.package-archive");
            installIntent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(installIntent);
        } catch (Exception exception) {
            Toast.makeText(this, "无法打开安装页面，请在下载通知中打开更新包", Toast.LENGTH_LONG).show();
        }
        if (forceUpdateRequired) {
            UpdateInfo retryInfo = pendingUpdateInfo == null
                    ? new UpdateInfo("发现新版本", "安装完成前请保持更新。", "最新版本", 0, apkUri.toString(), true)
                    : pendingUpdateInfo;
            retryInfo.message = "安装完成前请保持更新。";
            showForceUpdateDialog(retryInfo);
        }
    }

    private String basicAuth(String targetUsername, String targetPassword) {
        String raw = targetUsername + ":" + targetPassword;
        return "Basic " + Base64.encodeToString(raw.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line);
            }
        }
        return builder.toString();
    }

    private boolean credentialsMatchHost(String host) {
        try {
            return new URL(baseUrl).getHost().equals(host);
        } catch (Exception exception) {
            return false;
        }
    }

    private String viewerUrl() {
        return baseUrl + "/viewer/";
    }

    private String normalizeBaseUrl(String value) {
        String normalized = value == null ? "" : value.trim();
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private EditText createInput(String hint, int inputType) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setSingleLine(true);
        input.setTextSize(16);
        input.setTextColor(0xFF303030);
        input.setHintTextColor(0xFFC7C7C7);
        input.setInputType(inputType);
        input.setPadding(dp(10), 0, 0, 0);
        input.setBackground(new ColorDrawable(0x00000000));
        return input;
    }

    private View fieldBlock(String prefix, EditText input) {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        wrapper.addView(row, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        TextView label = new TextView(this);
        label.setText(prefix);
        label.setTextSize(16);
        label.setTextColor(0xFF9A9A9A);
        label.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(label, new LinearLayout.LayoutParams(dp(70), LinearLayout.LayoutParams.MATCH_PARENT));
        row.addView(input, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        View line = new View(this);
        line.setBackgroundColor(0xFFEDEDED);
        wrapper.addView(line, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                Math.max(1, dp(1))
        ));
        return wrapper;
    }

    private void addSocialButton(LinearLayout row, String label, int color) {
        TextView button = new TextView(this);
        button.setText(label);
        button.setTextSize(label.length() > 1 ? 14 : 24);
        button.setTextColor(color);
        button.setGravity(Gravity.CENTER);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setBackgroundResource(com.linkcollector.viewer.R.drawable.login_social_circle);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(56), dp(56));
        params.setMargins(dp(10), 0, dp(10), 0);
        row.addView(button, params);
    }

    private void updateLoginButtonState() {
        if (loginButton == null || serverInput == null || usernameInput == null || passwordInput == null) {
            return;
        }
        boolean complete = !serverInput.getText().toString().trim().isEmpty()
                && !usernameInput.getText().toString().trim().isEmpty()
                && !passwordInput.getText().toString().isEmpty();
        loginButton.setEnabled(complete);
        loginButton.setBackgroundResource(complete
                ? com.linkcollector.viewer.R.drawable.login_button_enabled
                : com.linkcollector.viewer.R.drawable.login_button_disabled);
        loginButton.setTextColor(complete ? 0xFFFFFFFF : 0xFFFFFFFF);
    }

    private LinearLayout.LayoutParams matchWidth(int height) {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                height
        );
    }

    private void setLoginStatus(String message, boolean error) {
        loginStatus.setText(message);
        loginStatus.setTextColor(error ? 0xFFC72239 : 0xFF666666);
    }

    private int dp(int value) {
        float density = getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }

    private void showError(String message) {
        runOnUiThread(() -> new AlertDialog.Builder(this)
                .setTitle("采集失败")
                .setMessage(message)
                .setPositiveButton("确定", null)
                .show());
    }

    private static class UpdateInfo {
        String title;
        String message;
        String latestVersionName;
        int latestVersionCode;
        String downloadUrl;
        boolean forceUpdate;

        UpdateInfo() {
        }

        UpdateInfo(
                String title,
                String message,
                String latestVersionName,
                int latestVersionCode,
                String downloadUrl,
                boolean forceUpdate
        ) {
            this.title = title;
            this.message = message;
            this.latestVersionName = latestVersionName;
            this.latestVersionCode = latestVersionCode;
            this.downloadUrl = downloadUrl;
            this.forceUpdate = forceUpdate;
        }
    }
}
