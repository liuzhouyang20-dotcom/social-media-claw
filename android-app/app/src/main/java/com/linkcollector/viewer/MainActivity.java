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
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ImageDecoder;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.Base64;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.SeekBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.VideoView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.net.URLEncoder;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String TAG = "LinkCollector";
    private static final String PREFS_NAME = "link_collector_auth";
    private static final String KEY_BASE_URL = "base_url";
    private static final String KEY_USERNAME = "username";
    private static final String KEY_PASSWORD = "password";
    private static final String DEFAULT_BASE_URL = "http://49.51.72.63";
    private static final String DEFAULT_USERNAME = "your-username";
    private static final int COLLECT_POLL_INTERVAL_MS = 3000;
    private static final String[] HOME_FILTERS = {"all", "xhs", "douyin", "image", "video"};
    private static final Pattern COLLECT_URL_PATTERN = Pattern.compile("https?://[^\\s\"'<>)\\]]+");
    private static final String[] HOME_FILTER_LABELS = {"全部", "小红书", "抖音", "图文", "视频"};
    private static final String[] SEARCH_PLATFORM_VALUES = {"all", "xhs", "douyin"};
    private static final String[] SEARCH_PLATFORM_LABELS = {"全部", "小红书", "抖音"};
    private static final String[] SEARCH_CONTENT_VALUES = {"all", "video", "image"};
    private static final String[] SEARCH_CONTENT_LABELS = {"全部", "视频", "图文"};

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newFixedThreadPool(4);
    private final Map<String, Bitmap> imageCache = new HashMap<>();
    private final Map<String, Float> imageRatioCache = new HashMap<>();
    private final List<FeedItem> items = new ArrayList<>();
    private final List<CollectTask> collectTasks = new ArrayList<>();
    private final List<SearchHistoryItem> searchHistory = new ArrayList<>();
    private final List<FeedItem> searchItems = new ArrayList<>();
    private final List<String> searchTerms = new ArrayList<>();
    private final Runnable collectPollRunnable = () -> {
        collectPollScheduled = false;
        pollCollectTasks();
    };

    private FrameLayout root;
    private LinearLayout appShell;
    private LinearLayout topNav;
    private LinearLayout bottomNav;
    private FrameLayout contentFrame;
    private ProgressBar progressBar;
    private View loginView;
    private TextView[] topTabLabels;
    private View[] topTabIndicators;
    private TextView homeTab;
    private TextView collectTab;
    private TextView chatTab;
    private TextView meTab;
    private EditText serverInput;
    private EditText usernameInput;
    private EditText passwordInput;
    private CheckBox agreementCheckBox;
    private TextView loginStatus;
    private Button loginButton;
    private EditText collectSourceInput;
    private CheckBox collectDownloadCheck;
    private TextView collectStatus;
    private TextView collectTaskCountLabel;
    private LinearLayout collectTaskList;
    private EditText searchInput;
    private TextView searchPlatformButton;
    private LinearLayout searchBody;
    private LinearLayout searchHistoryList;
    private TextView searchStatus;
    private LinearLayout searchTabs;
    private LinearLayout searchFilterPanel;
    private View detailView;
    private VideoView detailVideoView;
    private SharedPreferences preferences;

    private String baseUrl;
    private String username;
    private String password;
    private String pendingSharedText;
    private String activeView = "home";
    private String activeHomeFilter = "all";
    private int statusBarInset;
    private boolean refreshingItems;
    private boolean forceUpdateRequired;
    private UpdateInfo pendingUpdateInfo;
    private String searchPlatform = "all";
    private String searchContentType = "all";
    private String searchSort = "general";
    private String searchPublishTime = "all";
    private String searchDuration = "all";
    private boolean searchFiltersExpanded;
    private boolean searchingRemote;
    private boolean loadingMoreSearch;
    private boolean searchHasMore;
    private boolean collectPollScheduled;
    private volatile boolean collectPollRequestRunning;
    private String activeSearchCacheId = "";
    private JSONObject searchNextPage;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        pendingSharedText = extractSharedText(getIntent());
        buildUi();
        if (loadSavedCredentials()) {
            checkForUpdateThenShowApp();
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
        if (detailView != null && detailView.getParent() != null) {
            closeDetail();
            return;
        }
        if ("search".equals(activeView) || "collect".equals(activeView)) {
            showHome();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        mainHandler.removeCallbacks(collectPollRunnable);
        executor.shutdownNow();
    }

    private void buildUi() {
        statusBarInset = statusBarHeight();
        root = new FrameLayout(this);

        appShell = new LinearLayout(this);
        appShell.setOrientation(LinearLayout.VERTICAL);
        appShell.setBackgroundColor(0xFFFFFFFF);
        root.addView(appShell, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        topNav = createTopNav();
        appShell.addView(topNav, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                statusBarInset + dp(54)
        ));

        contentFrame = new FrameLayout(this);
        appShell.addView(contentFrame, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        bottomNav = createBottomNav();
        appShell.addView(bottomNav, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(86)
        ));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                Math.max(2, dp(2)),
                Gravity.TOP
        );
        progressParams.topMargin = statusBarInset;
        root.addView(progressBar, progressParams);

        loginView = createLoginView();
        root.addView(loginView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        setContentView(root);
    }

    private LinearLayout createTopNav() {
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setBackgroundColor(0xFFFFFFFF);
        container.setPadding(0, statusBarInset, 0, 0);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), 0, dp(10), 0);
        container.addView(row, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        ImageButton messageButton = createIconButton(com.linkcollector.viewer.R.drawable.ic_antd_message_outlined, "对话");
        messageButton.setOnClickListener(view -> Toast.makeText(this, "对话功能稍后开放", Toast.LENGTH_SHORT).show());
        row.addView(messageButton, new LinearLayout.LayoutParams(dp(46), LinearLayout.LayoutParams.MATCH_PARENT));

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabs.setGravity(Gravity.CENTER);
        topTabLabels = new TextView[HOME_FILTERS.length];
        topTabIndicators = new View[HOME_FILTERS.length];
        for (int i = 0; i < HOME_FILTERS.length; i++) {
            final int index = i;
            LinearLayout tab = createTopTab(HOME_FILTER_LABELS[i], i);
            tab.setOnClickListener(view -> {
                activeHomeFilter = HOME_FILTERS[index];
                selectTopFilter();
                renderHome();
            });
            tabs.addView(tab, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
        }
        row.addView(tabs, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        ImageButton searchButton = createIconButton(com.linkcollector.viewer.R.drawable.ic_antd_search_outlined, "搜索");
        searchButton.setOnClickListener(view -> showSearch(""));
        row.addView(searchButton, new LinearLayout.LayoutParams(dp(46), LinearLayout.LayoutParams.MATCH_PARENT));

        View line = new View(this);
        line.setBackgroundColor(0xFFEEEEEE);
        container.addView(line, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Math.max(1, dp(1))));
        selectTopFilter();
        return container;
    }

    private ImageButton createIconButton(int drawableRes, String description) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(drawableRes);
        button.setColorFilter(0xFF252525);
        button.setBackgroundColor(0x00000000);
        button.setPadding(dp(8), dp(8), dp(8), dp(8));
        button.setScaleType(ImageView.ScaleType.CENTER);
        button.setContentDescription(description);
        return button;
    }

    private LinearLayout createTopTab(String label, int index) {
        LinearLayout tab = new LinearLayout(this);
        tab.setOrientation(LinearLayout.VERTICAL);
        tab.setGravity(Gravity.CENTER);
        tab.setPadding(0, dp(5), 0, 0);

        TextView text = new TextView(this);
        text.setText(label);
        text.setTextSize(16);
        text.setSingleLine(true);
        text.setGravity(Gravity.CENTER);
        text.setIncludeFontPadding(false);
        tab.addView(text, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        View indicator = new View(this);
        GradientDrawable background = new GradientDrawable();
        background.setColor(0xFFFF2442);
        background.setCornerRadius(dp(2));
        indicator.setBackground(background);
        LinearLayout.LayoutParams indicatorParams = new LinearLayout.LayoutParams(dp(31), dp(3));
        indicatorParams.setMargins(0, 0, 0, dp(5));
        tab.addView(indicator, indicatorParams);

        topTabLabels[index] = text;
        topTabIndicators[index] = indicator;
        return tab;
    }

    private LinearLayout createBottomNav() {
        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setGravity(Gravity.CENTER);
        nav.setPadding(dp(20), dp(7), dp(20), dp(22));
        nav.setBackgroundColor(0xFFFFFFFF);

        homeTab = createNavText("首页");
        collectTab = createNavText("采集");
        View chatButton = createNativeChatButton();
        chatTab = createNavText("知识库");
        meTab = createNavText("设置");

        nav.addView(homeTab, navWeight());
        nav.addView(collectTab, navWeightWithMargins(0, dp(6)));
        nav.addView(chatButton, new LinearLayout.LayoutParams(dp(74), LinearLayout.LayoutParams.MATCH_PARENT));
        nav.addView(chatTab, navWeightWithMargins(dp(6), 0));
        nav.addView(meTab, navWeight());

        homeTab.setOnClickListener(view -> {
            showHome();
            refreshItems();
        });
        collectTab.setOnClickListener(view -> showCollect());
        chatButton.setOnClickListener(view -> Toast.makeText(this, "对话功能稍后开放", Toast.LENGTH_SHORT).show());
        chatTab.setOnClickListener(view -> Toast.makeText(this, "知识库功能稍后开放", Toast.LENGTH_SHORT).show());
        meTab.setOnClickListener(view -> showAccountMenu());
        return nav;
    }

    private LinearLayout.LayoutParams navWeight() {
        return new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1);
    }

    private LinearLayout.LayoutParams navWeightWithMargins(int left, int right) {
        LinearLayout.LayoutParams params = navWeight();
        params.setMargins(left, 0, right, 0);
        return params;
    }

    private TextView createNavText(String label) {
        TextView tab = new TextView(this);
        tab.setText(label);
        tab.setTextSize(16);
        tab.setGravity(Gravity.CENTER);
        tab.setSingleLine(true);
        tab.setTextColor(0xFF8C8C8C);
        return tab;
    }

    private View createNativeChatButton() {
        FrameLayout slot = new FrameLayout(this);
        slot.setContentDescription("对话");

        ImageButton button = new ImageButton(this);
        button.setImageResource(com.linkcollector.viewer.R.drawable.ic_antd_message_outlined);
        button.setColorFilter(0xFFFFFFFF);
        button.setBackgroundColor(0x00000000);
        button.setPadding(dp(11), dp(8), dp(11), dp(8));
        button.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
        button.setContentDescription("对话");
        button.setClickable(false);
        button.setFocusable(false);
        GradientDrawable background = new GradientDrawable();
        background.setColor(0xFFFF2442);
        background.setCornerRadius(dp(13));
        button.setBackground(background);
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(dp(52), dp(42), Gravity.CENTER);
        slot.addView(button, params);
        return slot;
    }

    private View createLoginView() {
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(0xFFFFFFFF);

        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setGravity(Gravity.CENTER_HORIZONTAL);
        container.setPadding(dp(34), dp(22), dp(34), dp(26));
        scrollView.addView(container, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT,
                ScrollView.LayoutParams.WRAP_CONTENT
        ));

        TextView mode = new TextView(this);
        mode.setText("切换服务器");
        mode.setTextSize(18);
        mode.setTextColor(0xFF9B9B9B);
        mode.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        container.addView(mode, matchWidth(dp(64)));

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

        agreementCheckBox = new CheckBox(this);
        agreementCheckBox.setButtonTintList(android.content.res.ColorStateList.valueOf(0xFF222222));
        agreementCheckBox.setOnCheckedChangeListener((buttonView, isChecked) -> updateLoginButtonState());
        agreement.addView(agreementCheckBox, new LinearLayout.LayoutParams(dp(24), dp(32)));

        TextView agreementText = new TextView(this);
        agreementText.setText("我已阅读并同意《用户协议》《隐私政策》");
        agreementText.setTextSize(14);
        agreementText.setTextColor(0xFF8A8A8A);
        agreementText.setOnClickListener(view -> agreementCheckBox.setChecked(!agreementCheckBox.isChecked()));
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

        TextWatcher watcher = new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { updateLoginButtonState(); }
            @Override public void afterTextChanged(Editable s) {}
        };
        serverInput.addTextChangedListener(watcher);
        usernameInput.addTextChangedListener(watcher);
        passwordInput.addTextChangedListener(watcher);
        updateLoginButtonState();
        return scrollView;
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
        wrapper.addView(row, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        TextView label = new TextView(this);
        label.setText(prefix);
        label.setTextSize(16);
        label.setTextColor(0xFF9A9A9A);
        label.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(label, new LinearLayout.LayoutParams(dp(70), LinearLayout.LayoutParams.MATCH_PARENT));
        row.addView(input, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        View line = new View(this);
        line.setBackgroundColor(0xFFEDEDED);
        wrapper.addView(line, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Math.max(1, dp(1))));
        return wrapper;
    }

    private void showLogin() {
        loginView.setVisibility(View.VISIBLE);
        appShell.setVisibility(View.GONE);
        progressBar.setVisibility(View.GONE);
        if (serverInput != null) serverInput.setText(preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL));
        if (usernameInput != null) usernameInput.setText(preferences.getString(KEY_USERNAME, DEFAULT_USERNAME));
        if (passwordInput != null) passwordInput.setText("");
        if (agreementCheckBox != null) agreementCheckBox.setChecked(false);
        updateLoginButtonState();
    }

    private void showApp() {
        loginView.setVisibility(View.GONE);
        appShell.setVisibility(View.VISIBLE);
        showHome();
        refreshItems();
        if (pendingSharedText != null && !pendingSharedText.trim().isEmpty()) {
            String text = pendingSharedText;
            pendingSharedText = null;
            collectSharedText(text);
        }
    }

    private void showHome() {
        activeView = "home";
        topNav.setVisibility(View.VISIBLE);
        bottomNav.setVisibility(View.VISIBLE);
        selectNativeTab("home");
        selectTopFilter();
        renderHome();
    }

    private void renderHome() {
        contentFrame.removeAllViews();
        contentFrame.addView(createFeedView(filteredItems("", activeHomeFilter)), new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
    }

    private View createFeedView(List<FeedItem> list) {
        FrameLayout refreshFrame = new FrameLayout(this);
        refreshFrame.setBackgroundColor(0xFFFFFFFF);

        TextView refreshHint = new TextView(this);
        refreshHint.setText("下拉刷新");
        refreshHint.setTextSize(14);
        refreshHint.setTextColor(0xFF888888);
        refreshHint.setGravity(Gravity.CENTER);
        refreshHint.setAlpha(0f);

        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(0xFFFFFFFF);
        scrollView.setOverScrollMode(View.OVER_SCROLL_ALWAYS);
        attachPullToRefresh(scrollView, refreshHint);

        LinearLayout columns = new LinearLayout(this);
        columns.setOrientation(LinearLayout.HORIZONTAL);
        columns.setPadding(dp(5), 0, dp(5), dp(14));
        scrollView.addView(columns, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT,
                ScrollView.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout left = createColumn();
        LinearLayout right = createColumn();
        columns.addView(left, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        columns.addView(right, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        if (list.isEmpty()) {
            LinearLayout emptyBox = new LinearLayout(this);
            emptyBox.setOrientation(LinearLayout.VERTICAL);
            emptyBox.setGravity(Gravity.CENTER);
            emptyBox.setPadding(dp(24), dp(40), dp(24), dp(40));

            TextView empty = new TextView(this);
            empty.setText("暂无匹配内容");
            empty.setTextSize(15);
            empty.setTextColor(0xFF888888);
            empty.setGravity(Gravity.CENTER);
            emptyBox.addView(empty, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
            ));

            TextView refresh = new TextView(this);
            refresh.setText("刷新");
            refresh.setTextSize(15);
            refresh.setTextColor(0xFFFFFFFF);
            refresh.setGravity(Gravity.CENTER);
            refresh.setBackground(pillBackground(0xFFFF2442));
            refresh.setOnClickListener(view -> refreshItems());
            LinearLayout.LayoutParams refreshParams = new LinearLayout.LayoutParams(dp(96), dp(38));
            refreshParams.setMargins(0, dp(18), 0, 0);
            emptyBox.addView(refresh, refreshParams);

            columns.removeAllViews();
            columns.addView(emptyBox, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    Math.max(dp(360), getResources().getDisplayMetrics().heightPixels - statusBarInset - dp(190))
            ));
            refreshFrame.addView(scrollView, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT
            ));
            refreshFrame.addView(refreshHint, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    dp(48),
                    Gravity.TOP
            ));
            return refreshFrame;
        }

        for (int i = 0; i < list.size(); i++) {
            FeedItem item = list.get(i);
            LinearLayout target = i % 2 == 0 ? left : right;
            target.addView(createFeedCard(item));
        }
        refreshFrame.addView(scrollView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        refreshFrame.addView(refreshHint, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                dp(48),
                Gravity.TOP
        ));
        return refreshFrame;
    }

    private LinearLayout createColumn() {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(3), 0, dp(3), 0);
        return column;
    }

    private View createFeedCard(FeedItem item) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(cardBackground(4, 0xFFFFFFFF, 0x0F000000));
        card.setClickable(true);
        card.setOnClickListener(view -> openDetail(item));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        cardParams.setMargins(0, 0, 0, dp(7));
        card.setLayoutParams(cardParams);

        FrameLayout mediaFrame = new FrameLayout(this);
        ImageView cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.CENTER_CROP);
        cover.setBackgroundColor(0xFFF1F1F1);
        mediaFrame.addView(cover, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                initialCoverHeight(item.cover)
        ));
        loadImageInto(item.cover, cover, false);
        if (item.isVideo) {
            ImageView playIcon = new ImageView(this);
            playIcon.setImageResource(com.linkcollector.viewer.R.drawable.ic_antd_caret_right_filled);
            playIcon.setColorFilter(0xFFFFFFFF);
            playIcon.setScaleType(ImageView.ScaleType.FIT_CENTER);
            int playSize = playIconSize();
            int playMargin = playIconMargin();
            FrameLayout.LayoutParams playParams = new FrameLayout.LayoutParams(playSize, playSize, Gravity.RIGHT | Gravity.TOP);
            playParams.setMargins(0, playMargin, playMargin, 0);
            mediaFrame.addView(playIcon, playParams);
        }
        card.addView(mediaFrame);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(7), dp(9), dp(7), dp(10));
        card.addView(body);

        TextView title = new TextView(this);
        title.setText(item.title);
        title.setTextSize(14);
        title.setTextColor(0xFF292929);
        title.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        title.setIncludeFontPadding(false);
        title.setLineSpacing(dp(2), 1.0f);
        title.setMaxLines(2);
        body.addView(title);

        LinearLayout meta = new LinearLayout(this);
        meta.setOrientation(LinearLayout.HORIZONTAL);
        meta.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams metaParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        metaParams.setMargins(0, dp(9), 0, 0);
        body.addView(meta, metaParams);

        ImageView avatar = new ImageView(this);
        avatar.setScaleType(ImageView.ScaleType.CENTER_CROP);
        avatar.setBackground(circleBackground(0xFFE6F0F4));
        meta.addView(avatar, new LinearLayout.LayoutParams(dp(19), dp(19)));
        loadImageInto(item.avatar, avatar, true);

        TextView author = new TextView(this);
        author.setText(item.author);
        author.setTextSize(13);
        author.setTextColor(0xFF8A8A8A);
        author.setSingleLine(true);
        LinearLayout.LayoutParams authorParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        authorParams.setMargins(dp(6), 0, dp(6), 0);
        meta.addView(author, authorParams);

        LinearLayout like = new LinearLayout(this);
        like.setOrientation(LinearLayout.HORIZONTAL);
        like.setGravity(Gravity.CENTER_VERTICAL);
        ImageView heart = new ImageView(this);
        heart.setImageResource(com.linkcollector.viewer.R.drawable.ic_antd_heart_outlined);
        heart.setColorFilter(0xFF8A8A8A);
        like.addView(heart, new LinearLayout.LayoutParams(dp(20), dp(20)));
        TextView likeCount = new TextView(this);
        likeCount.setText(fmt(item.liked));
        likeCount.setTextSize(13);
        likeCount.setTextColor(0xFF777777);
        likeCount.setSingleLine(true);
        LinearLayout.LayoutParams likeCountParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        likeCountParams.setMargins(dp(4), 0, 0, 0);
        like.addView(likeCount, likeCountParams);
        meta.addView(like);
        return card;
    }

    private int initialCoverHeight(String rawUrl) {
        if (rawUrl == null || rawUrl.trim().isEmpty()) {
            return dp(170);
        }
        String url = mediaUrl(rawUrl);
        Float ratio = imageRatioCache.get(url);
        if (ratio == null || ratio <= 0) {
            return dp(180);
        }
        return heightForRatio(ratio);
    }

    private int heightForRatio(float widthToHeight) {
        float targetRatio;
        float landscape = 4f / 3f;
        float square = 1f;
        float portrait = 3f / 4f;
        float landscapeDistance = Math.abs(widthToHeight - landscape);
        float squareDistance = Math.abs(widthToHeight - square);
        float portraitDistance = Math.abs(widthToHeight - portrait);
        if (landscapeDistance <= squareDistance && landscapeDistance <= portraitDistance) {
            targetRatio = landscape;
        } else if (portraitDistance <= squareDistance) {
            targetRatio = portrait;
        } else {
            targetRatio = square;
        }
        return Math.round(cardImageWidthPx() / targetRatio);
    }

    private int cardImageWidthPx() {
        int screenWidth = getResources().getDisplayMetrics().widthPixels;
        int horizontalSpace = dp(5) * 2 + dp(3) * 4;
        return Math.max(dp(120), (screenWidth - horizontalSpace) / 2);
    }

    private int playIconSize() {
        int target = Math.round(cardImageWidthPx() * 0.08f);
        return Math.max(dp(16), Math.min(dp(26), target));
    }

    private int playIconMargin() {
        int target = Math.round(cardImageWidthPx() * 0.035f);
        return Math.max(dp(7), Math.min(dp(12), target));
    }

    private void attachPullToRefresh(ScrollView scrollView, TextView refreshHint) {
        final float[] downY = new float[1];
        final boolean[] tracking = new boolean[1];
        scrollView.setOnTouchListener((view, event) -> {
            if (!"home".equals(activeView)) return false;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    tracking[0] = scrollView.getScrollY() == 0;
                    downY[0] = event.getY();
                    break;
                case MotionEvent.ACTION_MOVE:
                    if (tracking[0] && scrollView.getScrollY() == 0) {
                        float distance = Math.max(0f, event.getY() - downY[0]);
                        if (distance > dp(8)) {
                            refreshHint.setText(distance > dp(72) ? "松开刷新" : "下拉刷新");
                            refreshHint.setAlpha(Math.min(1f, distance / dp(72)));
                            refreshHint.setTranslationY(Math.min(dp(18), distance / 5f));
                        }
                    }
                    break;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (tracking[0] && scrollView.getScrollY() == 0 && event.getY() - downY[0] > dp(72)) {
                        refreshHint.setText("刷新中");
                        refreshHint.setAlpha(1f);
                        refreshHint.setTranslationY(0f);
                        refreshItems();
                    } else {
                        refreshHint.setAlpha(0f);
                        refreshHint.setTranslationY(0f);
                    }
                    tracking[0] = false;
                    break;
                default:
                    break;
            }
            return false;
        });
    }

    private void showSearch(String query) {
        activeView = "search";
        topNav.setVisibility(View.GONE);
        bottomNav.setVisibility(View.GONE);
        contentFrame.removeAllViews();
        searchItems.clear();

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(0, statusBarInset, 0, 0);
        page.setBackgroundColor(0xFFFFFFFF);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(8), dp(8), dp(10), dp(4));
        page.addView(row, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(58)));

        TextView back = new TextView(this);
        back.setText("‹");
        back.setTextSize(36);
        back.setGravity(Gravity.CENTER);
        back.setOnClickListener(view -> showHome());
        row.addView(back, new LinearLayout.LayoutParams(dp(34), LinearLayout.LayoutParams.MATCH_PARENT));

        LinearLayout searchBox = new LinearLayout(this);
        searchBox.setOrientation(LinearLayout.HORIZONTAL);
        searchBox.setGravity(Gravity.CENTER_VERTICAL);
        searchBox.setPadding(dp(10), 0, dp(8), 0);
        searchBox.setBackground(cardBackground(999, 0xFFF7F7F7, 0x12000000));
        row.addView(searchBox, new LinearLayout.LayoutParams(0, dp(44), 1));

        searchPlatformButton = new TextView(this);
        searchPlatformButton.setText(searchPlatformLabel());
        searchPlatformButton.setTextSize(14);
        searchPlatformButton.setTextColor(0xFF555555);
        searchPlatformButton.setGravity(Gravity.CENTER);
        searchPlatformButton.setSingleLine(true);
        searchPlatformButton.setOnClickListener(view -> chooseSearchPlatform());
        searchBox.addView(searchPlatformButton, new LinearLayout.LayoutParams(dp(58), LinearLayout.LayoutParams.MATCH_PARENT));

        View divider = new View(this);
        divider.setBackgroundColor(0xFFE6E6E6);
        searchBox.addView(divider, new LinearLayout.LayoutParams(Math.max(1, dp(1)), dp(22)));

        searchInput = new EditText(this);
        searchInput.setText(query);
        searchInput.setHint("搜索小红书 / 抖音作品");
        searchInput.setSingleLine(true);
        searchInput.setTextSize(17);
        searchInput.setTextColor(0xFF222222);
        searchInput.setHintTextColor(0xFF9B9B9B);
        searchInput.setPadding(dp(10), 0, dp(6), 0);
        searchInput.setBackground(new ColorDrawable(0x00000000));
        searchBox.addView(searchInput, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        TextView clear = new TextView(this);
        clear.setText("×");
        clear.setTextSize(23);
        clear.setTextColor(0xFF999999);
        clear.setGravity(Gravity.CENTER);
        clear.setOnClickListener(view -> {
            searchInput.setText("");
            searchItems.clear();
            renderSearchBody(false);
        });
        searchBox.addView(clear, new LinearLayout.LayoutParams(dp(30), LinearLayout.LayoutParams.MATCH_PARENT));

        TextView submit = new TextView(this);
        submit.setText("搜索");
        submit.setTextSize(17);
        submit.setTextColor(0xFFFF2442);
        submit.setGravity(Gravity.CENTER);
        submit.setOnClickListener(view -> performSearch(false));
        row.addView(submit, new LinearLayout.LayoutParams(dp(58), LinearLayout.LayoutParams.MATCH_PARENT));

        searchBody = new LinearLayout(this);
        searchBody.setOrientation(LinearLayout.VERTICAL);
        page.addView(searchBody, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        TextWatcher watcher = new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void afterTextChanged(Editable s) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                if (s.toString().trim().isEmpty()) {
                    searchItems.clear();
                    renderSearchBody(false);
                }
            }
        };
        searchInput.addTextChangedListener(watcher);
        contentFrame.addView(page);
        renderSearchBody(false);
        loadSearchHistory();
        loadSearchTrending();
        searchInput.requestFocus();
    }

    private void chooseSearchPlatform() {
        new AlertDialog.Builder(this)
                .setItems(SEARCH_PLATFORM_LABELS, (dialog, which) -> {
                    searchPlatform = SEARCH_PLATFORM_VALUES[which];
                    if (searchPlatformButton != null) searchPlatformButton.setText(searchPlatformLabel());
                    loadSearchHistory();
                })
                .show();
    }

    private String searchPlatformLabel() {
        if ("xhs".equals(searchPlatform)) return "小红书⌄";
        if ("douyin".equals(searchPlatform)) return "抖音⌄";
        return "全部⌄";
    }

    private void renderSearchBody(boolean resultsMode) {
        if (searchBody == null) return;
        searchBody.removeAllViews();
        if (resultsMode || !searchItems.isEmpty()) {
            renderSearchResults();
        } else {
            renderSearchLanding();
        }
    }

    private void renderSearchLanding() {
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(dp(18), dp(12), dp(18), dp(24));
        scrollView.addView(container);

        LinearLayout historyHeader = sectionHeader("历史记录", "清空");
        historyHeader.getChildAt(1).setOnClickListener(view -> deleteSearchHistory(""));
        container.addView(historyHeader);

        searchHistoryList = new LinearLayout(this);
        searchHistoryList.setOrientation(LinearLayout.VERTICAL);
        container.addView(searchHistoryList);
        renderSearchHistory();

        View line = new View(this);
        line.setBackgroundColor(0xFFF0F0F0);
        LinearLayout.LayoutParams lineParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Math.max(1, dp(1)));
        lineParams.setMargins(0, dp(14), 0, dp(14));
        container.addView(line, lineParams);

        container.addView(sectionHeader("猜你想搜", "换一换"));
        LinearLayout suggestions = new LinearLayout(this);
        suggestions.setOrientation(LinearLayout.VERTICAL);
        container.addView(suggestions);
        for (int i = 0; i < Math.min(8, searchTerms.size()); i += 2) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            suggestions.addView(row, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(46)));
            row.addView(suggestionText(searchTerms.get(i), i == 0 || i == 1), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
            if (i + 1 < searchTerms.size()) {
                row.addView(suggestionText(searchTerms.get(i + 1), i == 0 || i == 1), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
            }
        }
        searchBody.addView(scrollView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));
    }

    private LinearLayout sectionHeader(String title, String action) {
        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(20);
        titleView.setTextColor(0xFF707070);
        titleView.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        header.addView(titleView, new LinearLayout.LayoutParams(0, dp(42), 1));
        TextView actionView = new TextView(this);
        actionView.setText(action);
        actionView.setTextSize(15);
        actionView.setTextColor(0xFF8A8A8A);
        actionView.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        header.addView(actionView, new LinearLayout.LayoutParams(dp(78), dp(42)));
        return header;
    }

    private TextView suggestionText(String term, boolean hot) {
        TextView view = new TextView(this);
        view.setText(term);
        view.setTextSize(18);
        view.setTextColor(hot ? 0xFFFF2442 : 0xFF252525);
        view.setGravity(Gravity.CENTER_VERTICAL);
        view.setSingleLine(true);
        view.setOnClickListener(v -> {
            searchInput.setText(term);
            searchInput.setSelection(searchInput.length());
            performSearch(false);
        });
        return view;
    }

    private void renderSearchHistory() {
        if (searchHistoryList == null) return;
        searchHistoryList.removeAllViews();
        if (searchHistory.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("暂无搜索记录");
            empty.setTextSize(15);
            empty.setTextColor(0xFF999999);
            searchHistoryList.addView(empty, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(42)));
            return;
        }
        for (SearchHistoryItem item : searchHistory) {
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(0, 0, 0, 0);
            searchHistoryList.addView(row, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(48)));

            TextView label = new TextView(this);
            label.setText(item.keyword);
            label.setTextSize(17);
            label.setTextColor(0xFF252525);
            label.setSingleLine(true);
            label.setOnClickListener(view -> openCachedSearch(item));
            row.addView(label, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

            TextView meta = new TextView(this);
            meta.setText(item.metaText());
            meta.setTextSize(12);
            meta.setTextColor(0xFF999999);
            meta.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
            row.addView(meta, new LinearLayout.LayoutParams(dp(96), LinearLayout.LayoutParams.MATCH_PARENT));

            TextView delete = new TextView(this);
            delete.setText("×");
            delete.setTextSize(20);
            delete.setTextColor(0xFFAAAAAA);
            delete.setGravity(Gravity.CENTER);
            delete.setOnClickListener(view -> deleteSearchHistory(item.id));
            row.addView(delete, new LinearLayout.LayoutParams(dp(34), LinearLayout.LayoutParams.MATCH_PARENT));
        }
    }

    private void renderSearchResults() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        searchBody.addView(page, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));

        searchTabs = new LinearLayout(this);
        searchTabs.setOrientation(LinearLayout.HORIZONTAL);
        searchTabs.setPadding(dp(12), 0, dp(12), 0);
        page.addView(searchTabs, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(48)));
        for (int i = 0; i < SEARCH_CONTENT_VALUES.length; i++) {
            final String value = SEARCH_CONTENT_VALUES[i];
            TextView tab = createSearchTab(SEARCH_CONTENT_LABELS[i], value.equals(searchContentType));
            tab.setOnClickListener(view -> {
                searchContentType = value;
                performSearch(false);
            });
            searchTabs.addView(tab, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
        }
        TextView filter = createSearchTab(searchFiltersExpanded ? "收起" : "筛选", searchFiltersExpanded);
        filter.setOnClickListener(view -> {
            searchFiltersExpanded = !searchFiltersExpanded;
            renderSearchBody(true);
        });
        searchTabs.addView(filter, new LinearLayout.LayoutParams(dp(68), LinearLayout.LayoutParams.MATCH_PARENT));

        if (searchFiltersExpanded) {
            searchFilterPanel = new LinearLayout(this);
            searchFilterPanel.setOrientation(LinearLayout.VERTICAL);
            searchFilterPanel.setPadding(dp(18), dp(6), dp(18), dp(12));
            page.addView(searchFilterPanel, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            renderSearchFilterPanel();
        }

        searchStatus = new TextView(this);
        searchStatus.setText(searchStatusText());
        searchStatus.setTextSize(13);
        searchStatus.setTextColor(0xFF888888);
        searchStatus.setPadding(dp(18), dp(4), dp(18), dp(6));
        page.addView(searchStatus);

        page.addView(createSearchFeedView(searchItems), new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
    }

    private String searchStatusText() {
        if (searchingRemote) return "正在搜索...";
        if (loadingMoreSearch) return "正在加载下一页...";
        if (searchItems.isEmpty()) return "暂无搜索结果";
        return searchHasMore ? "点击卡片采集，滑到底自动加载更多" : "点击卡片可直接加入采集队列";
    }

    private TextView createSearchTab(String label, boolean active) {
        TextView tab = new TextView(this);
        tab.setText(label);
        tab.setTextSize(17);
        tab.setGravity(Gravity.CENTER);
        tab.setSingleLine(true);
        tab.setTextColor(active ? 0xFF222222 : 0xFF777777);
        tab.setTypeface(Typeface.DEFAULT, active ? Typeface.BOLD : Typeface.NORMAL);
        return tab;
    }

    private void renderSearchFilterPanel() {
        if (searchFilterPanel == null) return;
        searchFilterPanel.removeAllViews();
        searchFilterPanel.addView(filterGroup(
                "排序依据",
                new String[]{"综合", "最新发布", "最多点赞", "最多评论", "最多收藏"},
                new String[]{"general", "latest", "likes", "comments", "collects"},
                searchSort,
                value -> {
                    searchSort = value;
                    performSearch(false);
                }
        ));
        searchFilterPanel.addView(filterGroup(
                "发布时间",
                new String[]{"不限", "一天内", "一周内", "半年内"},
                new String[]{"all", "day", "week", "half_year"},
                searchPublishTime,
                value -> {
                    searchPublishTime = value;
                    performSearch(false);
                }
        ));
        if (!"xhs".equals(searchPlatform)) {
            searchFilterPanel.addView(filterGroup(
                    "视频时长",
                    new String[]{"不限", "1分钟以内", "1-5分钟", "5分钟以上"},
                    new String[]{"all", "short", "medium", "long"},
                    searchDuration,
                    value -> {
                        searchDuration = value;
                        performSearch(false);
                    }
            ));
        }
        LinearLayout actions = new LinearLayout(this);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        searchFilterPanel.addView(actions, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(48)));
        TextView reset = new TextView(this);
        reset.setText("↻ 重置");
        reset.setTextSize(16);
        reset.setGravity(Gravity.CENTER);
        reset.setTextColor(0xFF333333);
        reset.setOnClickListener(view -> {
            searchSort = "general";
            searchPublishTime = "all";
            searchDuration = "all";
            performSearch(false);
        });
        actions.addView(reset, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
        TextView collapse = new TextView(this);
        collapse.setText("⌃ 收起");
        collapse.setTextSize(16);
        collapse.setGravity(Gravity.CENTER);
        collapse.setTextColor(0xFF333333);
        collapse.setOnClickListener(view -> {
            searchFiltersExpanded = false;
            renderSearchBody(true);
        });
        actions.addView(collapse, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
    }

    private View filterGroup(String title, String[] labels, String[] values, String current, ValueHandler handler) {
        LinearLayout group = new LinearLayout(this);
        group.setOrientation(LinearLayout.VERTICAL);
        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(16);
        titleView.setTextColor(0xFF777777);
        group.addView(titleView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(36)));
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        group.addView(row, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(44)));
        for (int i = 0; i < labels.length; i++) {
            String value = values[i];
            TextView chip = new TextView(this);
            chip.setText(labels[i]);
            chip.setTextSize(14);
            chip.setSingleLine(true);
            chip.setGravity(Gravity.CENTER);
            boolean active = value.equals(current);
            chip.setTextColor(active ? 0xFFFF2442 : 0xFF555555);
            chip.setBackground(cardBackground(4, active ? 0xFFFFEEF2 : 0xFFF5F5F5, 0x00000000));
            chip.setOnClickListener(view -> handler.apply(value));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, dp(36), 1);
            params.setMargins(dp(3), 0, dp(3), 0);
            row.addView(chip, params);
        }
        return group;
    }

    private View createSearchFeedView(List<FeedItem> list) {
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(0xFFFFFFFF);
        scrollView.getViewTreeObserver().addOnScrollChangedListener(() -> {
            View child = scrollView.getChildAt(0);
            if (child == null) return;
            int remaining = child.getBottom() - (scrollView.getHeight() + scrollView.getScrollY());
            if (remaining < dp(220)) {
                loadMoreSearchResults();
            }
        });

        LinearLayout columns = new LinearLayout(this);
        columns.setOrientation(LinearLayout.HORIZONTAL);
        columns.setPadding(dp(5), 0, dp(5), dp(14));
        scrollView.addView(columns, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT,
                ScrollView.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout left = createColumn();
        LinearLayout right = createColumn();
        columns.addView(left, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        columns.addView(right, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        if (list.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText(searchingRemote ? "正在加载搜索结果..." : "没有找到可采集作品");
            empty.setTextSize(15);
            empty.setTextColor(0xFF888888);
            empty.setGravity(Gravity.CENTER);
            columns.removeAllViews();
            columns.addView(empty, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(240)));
            return scrollView;
        }

        for (int i = 0; i < list.size(); i++) {
            FeedItem item = list.get(i);
            LinearLayout target = i % 2 == 0 ? left : right;
            target.addView(createSearchFeedCard(item));
        }
        if (loadingMoreSearch || searchHasMore) {
            TextView footer = new TextView(this);
            footer.setText(loadingMoreSearch ? "正在加载更多..." : "继续向下滑动加载更多");
            footer.setTextSize(14);
            footer.setTextColor(0xFF999999);
            footer.setGravity(Gravity.CENTER);
            columns.addView(footer, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(54)));
        }
        return scrollView;
    }

    private View createSearchFeedCard(FeedItem item) {
        View card = createFeedCard(item);
        card.setOnClickListener(view -> collectSearchResult(item));
        return card;
    }

    private void performSearch(boolean fromCacheOnly) {
        activeSearchCacheId = "";
        searchNextPage = null;
        searchHasMore = false;
        loadingMoreSearch = false;
        performSearchRequest(false);
    }

    private void performSearchRequest(boolean append) {
        String keyword = searchInput == null ? "" : searchInput.getText().toString().trim();
        if (keyword.isEmpty()) {
            Toast.makeText(this, "请输入关键词", Toast.LENGTH_SHORT).show();
            return;
        }
        if (append && (!searchHasMore || loadingMoreSearch || searchingRemote || activeSearchCacheId.isEmpty())) return;
        if (append) loadingMoreSearch = true;
        else searchingRemote = true;
        renderSearchBody(true);
        executor.execute(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("keyword", keyword);
                payload.put("platform", searchPlatform);
                payload.put("contentType", searchContentType);
                payload.put("sort", searchSort);
                payload.put("publishTime", searchPublishTime);
                payload.put("duration", searchDuration);
                payload.put("page", 1);
                if (append) {
                    payload.put("appendTo", activeSearchCacheId);
                    if (searchNextPage != null) payload.put("pageState", searchNextPage);
                }
                JSONObject response = postJson(baseUrl + "/api/search", payload);
                if (!response.optBoolean("ok")) throw new RuntimeException(response.optString("error", "搜索失败"));
                List<FeedItem> next = parseFeedItems(append ? response.optJSONArray("pageItems") : response.optJSONArray("items"));
                JSONObject record = response.optJSONObject("record");
                JSONObject nextPage = response.optJSONObject("nextPage");
                mainHandler.post(() -> {
                    if (append) {
                        appendSearchItems(next);
                    } else {
                        searchItems.clear();
                        searchItems.addAll(next);
                    }
                    if (record != null) activeSearchCacheId = record.optString("id", activeSearchCacheId);
                    searchNextPage = nextPage;
                    searchHasMore = nextPage != null && nextPage.optBoolean("hasMore", false);
                    searchingRemote = false;
                    loadingMoreSearch = false;
                    renderSearchBody(true);
                    loadSearchHistory();
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    searchingRemote = false;
                    loadingMoreSearch = false;
                    renderSearchBody(true);
                    Toast.makeText(this, exception.getMessage() == null ? "搜索失败" : exception.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private void loadMoreSearchResults() {
        performSearchRequest(true);
    }

    private void appendSearchItems(List<FeedItem> next) {
        Map<String, Boolean> seen = new HashMap<>();
        for (FeedItem item : searchItems) {
            seen.put(item.platform + ":" + item.source + ":" + item.title, true);
        }
        for (FeedItem item : next) {
            String key = item.platform + ":" + item.source + ":" + item.title;
            if (seen.containsKey(key)) continue;
            seen.put(key, true);
            searchItems.add(item);
        }
    }

    private List<FeedItem> parseFeedItems(JSONArray array) {
        List<FeedItem> next = new ArrayList<>();
        if (array == null) return next;
        for (int i = 0; i < array.length(); i++) {
            next.add(FeedItem.fromJson(array.optJSONObject(i)));
        }
        return next;
    }

    private void loadSearchHistory() {
        if (baseUrl == null || baseUrl.trim().isEmpty()) return;
        executor.execute(() -> {
            try {
                JSONObject response = getJson(baseUrl + "/api/search-history?platform=" + encodeParam(searchPlatform));
                JSONArray array = response.optJSONArray("history");
                List<SearchHistoryItem> next = new ArrayList<>();
                if (array != null) {
                    for (int i = 0; i < array.length(); i++) {
                        next.add(SearchHistoryItem.fromJson(array.optJSONObject(i)));
                    }
                }
                mainHandler.post(() -> {
                    searchHistory.clear();
                    searchHistory.addAll(next);
                    renderSearchHistory();
                });
            } catch (Exception ignored) {
            }
        });
    }

    private void loadSearchTrending() {
        if (!searchTerms.isEmpty()) return;
        executor.execute(() -> {
            List<String> next = new ArrayList<>();
            try {
                JSONObject response = getJson(baseUrl + "/api/search-trending");
                JSONArray array = response.optJSONArray("terms");
                if (array != null) {
                    for (int i = 0; i < array.length(); i++) next.add(array.optString(i));
                }
            } catch (Exception ignored) {
            }
            if (next.isEmpty()) {
                next.add("codex");
                next.add("AI 工作流");
                next.add("小红书运营");
                next.add("抖音热门视频");
            }
            mainHandler.post(() -> {
                searchTerms.clear();
                searchTerms.addAll(next);
                if ("search".equals(activeView) && searchItems.isEmpty()) renderSearchBody(false);
            });
        });
    }

    private void openCachedSearch(SearchHistoryItem item) {
        searchInput.setText(item.keyword);
        searchPlatform = item.platform;
        if (searchPlatformButton != null) searchPlatformButton.setText(searchPlatformLabel());
        searchingRemote = true;
        renderSearchBody(true);
        executor.execute(() -> {
            try {
                JSONObject response = getJson(baseUrl + "/api/search-result?id=" + encodeParam(item.id));
                if (!response.optBoolean("ok")) throw new RuntimeException(response.optString("error", "缓存读取失败"));
                List<FeedItem> next = parseFeedItems(response.optJSONArray("items"));
                JSONObject record = response.optJSONObject("record");
                JSONObject nextPage = response.optJSONObject("nextPage");
                mainHandler.post(() -> {
                    searchItems.clear();
                    searchItems.addAll(next);
                    activeSearchCacheId = record == null ? item.id : record.optString("id", item.id);
                    searchNextPage = nextPage;
                    searchHasMore = nextPage != null && nextPage.optBoolean("hasMore", false);
                    searchingRemote = false;
                    renderSearchBody(true);
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    searchingRemote = false;
                    searchHasMore = false;
                    searchNextPage = null;
                    renderSearchBody(false);
                    Toast.makeText(this, exception.getMessage() == null ? "缓存已过期" : exception.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private void deleteSearchHistory(String id) {
        executor.execute(() -> {
            try {
                String target = baseUrl + "/api/search-history" + (id == null || id.isEmpty() ? "" : "?id=" + encodeParam(id));
                deleteJson(target);
                mainHandler.post(this::loadSearchHistory);
            } catch (Exception exception) {
                mainHandler.post(() -> Toast.makeText(this, "删除失败", Toast.LENGTH_SHORT).show());
            }
        });
    }

    private void collectSearchResult(FeedItem item) {
        if (item.source == null || item.source.trim().isEmpty()) {
            Toast.makeText(this, "该结果暂不能采集", Toast.LENGTH_SHORT).show();
            return;
        }
        executor.execute(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("source", item.source);
                payload.put("platform", item.platform == null || item.platform.isEmpty() ? "auto" : item.platform);
                payload.put("contentType", item.isVideo ? "video" : "image");
                payload.put("downloadMedia", true);
                JSONObject response = postJson(baseUrl + "/api/collect", payload);
                if (!response.optBoolean("ok")) throw new RuntimeException(response.optString("error", "采集失败"));
                mainHandler.post(() -> Toast.makeText(this, "已加入采集队列", Toast.LENGTH_SHORT).show());
            } catch (Exception exception) {
                mainHandler.post(() -> Toast.makeText(this, exception.getMessage() == null ? "采集失败" : exception.getMessage(), Toast.LENGTH_LONG).show());
            }
        });
    }

    private String encodeParam(String value) throws Exception {
        return URLEncoder.encode(value == null ? "" : value, "UTF-8");
    }

    private interface ValueHandler {
        void apply(String value);
    }

    private void showCollect() {
        activeView = "collect";
        topNav.setVisibility(View.GONE);
        bottomNav.setVisibility(View.VISIBLE);
        selectNativeTab("collect");
        contentFrame.removeAllViews();

        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(0xFFF4F5F2);
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(dp(18), statusBarInset + dp(18), dp(18), dp(112));
        scrollView.addView(container);

        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setPadding(dp(18), dp(18), dp(18), dp(20));
        hero.setBackground(cardBackground(10, 0xFFFFFFFF, 0x0F000000));
        container.addView(hero, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setGravity(Gravity.BOTTOM);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        hero.addView(titleRow, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(42)));

        TextView title = new TextView(this);
        title.setText("采集新内容");
        title.setTextSize(31);
        title.setTextColor(0xFF151817);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setIncludeFontPadding(false);
        titleRow.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView platformPill = collectPill("小红书 / 抖音", 0xFFFFF1F3, 0xFFFF2442);
        LinearLayout.LayoutParams platformParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(30));
        platformParams.setMargins(dp(10), 0, 0, dp(2));
        titleRow.addView(platformPill, platformParams);

        TextView copy = new TextView(this);
        copy.setText("粘贴一条或多条分享链接，系统会在后台逐条解析、下载媒体并同步到首页。");
        copy.setTextSize(15);
        copy.setTextColor(0xFF69716E);
        copy.setLineSpacing(dp(3), 1.0f);
        LinearLayout.LayoutParams copyParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        copyParams.setMargins(0, dp(10), 0, dp(16));
        hero.addView(copy, copyParams);

        LinearLayout inputPanel = new LinearLayout(this);
        inputPanel.setOrientation(LinearLayout.VERTICAL);
        inputPanel.setPadding(dp(14), dp(12), dp(14), dp(10));
        inputPanel.setBackground(cardBackground(10, 0xFFFAFBF8, 0xFFD9DFDA));
        hero.addView(inputPanel, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView inputLabel = new TextView(this);
        inputLabel.setText("分享口令或链接，可批量粘贴");
        inputLabel.setTextSize(13);
        inputLabel.setTextColor(0xFF717B77);
        inputLabel.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        inputPanel.addView(inputLabel);

        collectSourceInput = new EditText(this);
        collectSourceInput.setHint("每行一条，或直接粘贴包含多条链接的文本");
        collectSourceInput.setMinLines(4);
        collectSourceInput.setGravity(Gravity.TOP);
        collectSourceInput.setTextSize(16);
        collectSourceInput.setTextColor(0xFF202423);
        collectSourceInput.setHintTextColor(0xFF9AA39E);
        collectSourceInput.setPadding(0, dp(8), 0, 0);
        collectSourceInput.setBackground(new ColorDrawable(0x00000000));
        inputPanel.addView(collectSourceInput, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(112)));

        LinearLayout optionRow = new LinearLayout(this);
        optionRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams optionParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(46));
        optionParams.setMargins(0, dp(10), 0, 0);
        hero.addView(optionRow, optionParams);

        collectDownloadCheck = new CheckBox(this);
        collectDownloadCheck.setText("下载媒体文件");
        collectDownloadCheck.setTextSize(15);
        collectDownloadCheck.setTextColor(0xFF28302D);
        collectDownloadCheck.setChecked(true);
        collectDownloadCheck.setButtonTintList(android.content.res.ColorStateList.valueOf(0xFF0F8B7B));
        optionRow.addView(collectDownloadCheck, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        TextView queueHint = collectPill("后台队列", 0xFFEFEFED, 0xFF68716E);
        optionRow.addView(queueHint, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(30)));

        Button submit = new Button(this);
        submit.setText("开始采集");
        submit.setAllCaps(false);
        submit.setTextSize(18);
        submit.setTextColor(0xFFFFFFFF);
        submit.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        submit.setBackgroundResource(com.linkcollector.viewer.R.drawable.login_button_enabled);
        submit.setOnClickListener(view -> submitCollect());
        LinearLayout.LayoutParams submitParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(50));
        submitParams.setMargins(0, dp(6), 0, dp(14));
        hero.addView(submit, submitParams);

        collectStatus = new TextView(this);
        collectStatus.setText("等待新的分享口令。");
        collectStatus.setTextSize(13);
        collectStatus.setTextColor(0xFF69716E);
        collectStatus.setGravity(Gravity.CENTER);
        hero.addView(collectStatus, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        LinearLayout taskHeader = new LinearLayout(this);
        taskHeader.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams taskHeaderParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        taskHeaderParams.setMargins(0, dp(22), 0, dp(6));
        container.addView(taskHeader, taskHeaderParams);

        TextView taskTitle = new TextView(this);
        taskTitle.setText("采集记录");
        taskTitle.setTextSize(20);
        taskTitle.setTextColor(0xFF252A28);
        taskTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        taskHeader.addView(taskTitle, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));

        collectTaskCountLabel = collectPill(collectTasks.size() + " 条", 0xFFFFFFFF, 0xFF69716E);
        taskHeader.addView(collectTaskCountLabel, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(30)));

        collectTaskList = new LinearLayout(this);
        collectTaskList.setOrientation(LinearLayout.VERTICAL);
        container.addView(collectTaskList);
        renderCollectTasks();
        pollCollectTasks();
        contentFrame.addView(scrollView);
    }

    private void openDetail(FeedItem item) {
        topNav.setVisibility(View.GONE);
        bottomNav.setVisibility(View.GONE);
        if (item.isVideo && !item.hasPlayableVideo()) {
            Toast.makeText(this, "这条视频还没有采集到可播放文件", Toast.LENGTH_SHORT).show();
        }
        detailView = item.isVideo ? createVideoDetailView(item) : createDetailView(item);
        root.addView(detailView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
    }

    private View createVideoDetailView(FeedItem item) {
        FrameLayout page = new FrameLayout(this);
        page.setBackgroundColor(0xFF000000);

        FrameLayout mediaLayer = new FrameLayout(this);
        page.addView(mediaLayer, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        ImageView poster = new ImageView(this);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        poster.setBackgroundColor(0xFF101010);
        mediaLayer.addView(poster, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        loadImageInto(item.cover, poster, false);

        VideoView video = new VideoView(this);
        video.setVisibility(View.INVISIBLE);
        mediaLayer.addView(video, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
                Gravity.CENTER
        ));

        TextView playbackStatus = new TextView(this);
        playbackStatus.setText("正在加载视频...");
        playbackStatus.setTextColor(0xDFFFFFFF);
        playbackStatus.setTextSize(15);
        playbackStatus.setGravity(Gravity.CENTER);
        playbackStatus.setPadding(dp(28), 0, dp(28), 0);
        mediaLayer.addView(playbackStatus, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
        ));

        View topShade = new View(this);
        topShade.setBackgroundColor(0x00000000);
        FrameLayout.LayoutParams topShadeParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                statusBarInset + dp(72),
                Gravity.TOP
        );
        page.addView(topShade, topShadeParams);

        LinearLayout topBar = new LinearLayout(this);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setPadding(dp(10), statusBarInset, dp(14), 0);
        page.addView(topBar, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                statusBarInset + dp(56),
                Gravity.TOP
        ));

        TextView back = new TextView(this);
        back.setText("‹");
        back.setTextColor(0xEFFFFFFF);
        back.setTextSize(42);
        back.setGravity(Gravity.CENTER);
        back.setIncludeFontPadding(false);
        back.setOnClickListener(view -> closeDetailToHome());
        topBar.addView(back, new LinearLayout.LayoutParams(dp(44), LinearLayout.LayoutParams.MATCH_PARENT));

        View topSpace = new View(this);
        topBar.addView(topSpace, new LinearLayout.LayoutParams(0, 1, 1));

        TextView marker = new TextView(this);
        marker.setText("⋯");
        marker.setTextColor(0xEFFFFFFF);
        marker.setTextSize(30);
        marker.setGravity(Gravity.CENTER);
        marker.setIncludeFontPadding(false);
        topBar.addView(marker, new LinearLayout.LayoutParams(dp(46), LinearLayout.LayoutParams.MATCH_PARENT));

        LinearLayout bottomInfo = new LinearLayout(this);
        bottomInfo.setOrientation(LinearLayout.VERTICAL);
        bottomInfo.setPadding(dp(18), 0, dp(18), 0);
        FrameLayout.LayoutParams infoParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
        );
        infoParams.setMargins(0, 0, 0, dp(96));
        page.addView(bottomInfo, infoParams);

        LinearLayout authorRow = new LinearLayout(this);
        authorRow.setGravity(Gravity.CENTER_VERTICAL);
        bottomInfo.addView(authorRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(54)
        ));

        ImageView avatar = new ImageView(this);
        avatar.setBackground(circleBackground(0xFFE6F0F4));
        authorRow.addView(avatar, new LinearLayout.LayoutParams(dp(46), dp(46)));
        loadImageInto(item.avatar, avatar, true);

        TextView author = new TextView(this);
        author.setText(item.author.isEmpty() ? "作者" : item.author);
        author.setTextColor(0xFFFFFFFF);
        author.setTextSize(18);
        author.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        author.setSingleLine(true);
        LinearLayout.LayoutParams authorParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        authorParams.setMargins(dp(12), 0, dp(10), 0);
        authorRow.addView(author, authorParams);

        Button follow = new Button(this);
        follow.setText("关注");
        follow.setAllCaps(false);
        follow.setTextSize(15);
        follow.setTextColor(0xFFFFFFFF);
        follow.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        follow.setPadding(dp(14), 0, dp(14), 0);
        follow.setBackground(pillBackground(0xFFFF2442));
        LinearLayout.LayoutParams followParams = new LinearLayout.LayoutParams(dp(74), dp(38));
        followParams.setMargins(0, 0, dp(12), 0);
        authorRow.addView(follow, followParams);

        TextView duration = new TextView(this);
        duration.setText("--:--");
        duration.setTextColor(0xBFFFFFFF);
        duration.setTextSize(16);
        duration.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        authorRow.addView(duration, new LinearLayout.LayoutParams(dp(58), LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView description = new TextView(this);
        description.setText(item.description.isEmpty() ? item.title : item.description);
        description.setTextColor(0xEFFFFFFF);
        description.setTextSize(17);
        description.setLineSpacing(dp(3), 1.0f);
        description.setMaxLines(2);
        bottomInfo.addView(description, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        SeekBar progress = new SeekBar(this);
        progress.setMax(1000);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                dp(28),
                Gravity.BOTTOM
        );
        progressParams.setMargins(dp(18), 0, dp(18), dp(72));
        page.addView(progress, progressParams);

        LinearLayout actions = new LinearLayout(this);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        actions.setPadding(dp(16), dp(7), dp(16), dp(12));
        actions.setBackgroundColor(0xFF000000);
        page.addView(actions, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                dp(72),
                Gravity.BOTTOM
        ));

        TextView analyze = new TextView(this);
        analyze.setText("深度分析");
        analyze.setTextSize(16);
        analyze.setTextColor(0xFFFFFFFF);
        analyze.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        analyze.setGravity(Gravity.CENTER);
        analyze.setPadding(dp(16), 0, dp(16), 0);
        analyze.setBackground(pillBackground(0xFF1A1A1A));
        analyze.setOnClickListener(view -> Toast.makeText(this, "深度分析功能稍后开放", Toast.LENGTH_SHORT).show());
        actions.addView(analyze, new LinearLayout.LayoutParams(0, dp(46), 1));

        actions.addView(videoAction(com.linkcollector.viewer.R.drawable.ic_antd_heart_outlined, fmt(item.liked)));
        actions.addView(videoTextAction("☆", fmt(item.collected)));
        actions.addView(videoTextAction("☻", fmt(item.comments)));

        final boolean[] dragging = {false};
        final Runnable[] updateProgress = new Runnable[1];
        updateProgress[0] = () -> {
            if (detailView != page) return;
            if (!dragging[0] && video.getDuration() > 0) {
                int position = video.getCurrentPosition();
                int total = video.getDuration();
                progress.setProgress(Math.min(1000, Math.max(0, position * 1000 / total)));
            }
            mainHandler.postDelayed(updateProgress[0], 500);
        };
        progress.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar seekBar, int progressValue, boolean fromUser) {
                if (fromUser && video.getDuration() > 0) {
                    video.seekTo(video.getDuration() * progressValue / 1000);
                }
            }
            @Override public void onStartTrackingTouch(SeekBar seekBar) {
                dragging[0] = true;
            }
            @Override public void onStopTrackingTouch(SeekBar seekBar) {
                dragging[0] = false;
                if (video.getDuration() > 0) {
                    video.seekTo(video.getDuration() * seekBar.getProgress() / 1000);
                }
            }
        });

        if (item.hasPlayableVideo()) {
            detailVideoView = video;
            final String[] activeVideo = {item.playableVideo()};
            setVideoSource(video, activeVideo[0]);
            video.setOnPreparedListener((MediaPlayer mp) -> {
                mp.setLooping(true);
                duration.setText(formatDuration(mp.getDuration()));
                playbackStatus.setVisibility(View.GONE);
                poster.setVisibility(View.GONE);
                video.setVisibility(View.VISIBLE);
                video.start();
                mainHandler.post(updateProgress[0]);
            });
            video.setOnErrorListener((mp, what, extra) -> {
                Log.w(TAG, "Video playback failed url=" + mediaUrl(activeVideo[0]) + " what=" + what + " extra=" + extra);
                playbackStatus.setText("本地视频文件暂时无法播放");
                playbackStatus.setVisibility(View.VISIBLE);
                Toast.makeText(this, "本地视频文件暂时无法播放：" + what + "/" + extra, Toast.LENGTH_SHORT).show();
                return true;
            });
        } else {
            duration.setText("--:--");
            playbackStatus.setText("这条视频还没有采集到可播放文件");
        }
        return page;
    }

    private void setVideoSource(VideoView video, String rawUrl) {
        String url = mediaUrl(rawUrl);
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", "Mozilla/5.0 LinkCollectorViewer/1.0");
        headers.put("Accept", "video/mp4,video/*;q=0.9,*/*;q=0.8");
        video.setVideoURI(Uri.parse(url), headers);
    }

    private View createDetailView(FeedItem item) {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(0xFFFFFFFF);

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(8), statusBarInset, dp(16), 0);
        page.addView(bar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, statusBarInset + dp(54)));

        TextView back = new TextView(this);
        back.setText("‹");
        back.setTextSize(38);
        back.setGravity(Gravity.CENTER);
        back.setOnClickListener(view -> closeDetailToHome());
        bar.addView(back, new LinearLayout.LayoutParams(dp(42), LinearLayout.LayoutParams.MATCH_PARENT));

        ImageView avatar = new ImageView(this);
        avatar.setBackground(circleBackground(0xFFE6F0F4));
        bar.addView(avatar, new LinearLayout.LayoutParams(dp(30), dp(30)));
        loadImageInto(item.avatar, avatar, true);

        TextView author = new TextView(this);
        author.setText(item.author);
        author.setTextSize(16);
        author.setTextColor(0xFF333333);
        author.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams authorParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        authorParams.setMargins(dp(10), 0, 0, 0);
        bar.addView(author, authorParams);

        ScrollView scroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(22), 0, dp(22), dp(24));
        scroll.addView(body);
        page.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        ImageView cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.CENTER_CROP);
        cover.setBackgroundColor(0xFFF1F1F1);
        body.addView(cover, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(360)));
        loadImageInto(item.cover, cover, false);

        TextView title = new TextView(this);
        title.setText(item.title);
        title.setTextSize(24);
        title.setTextColor(0xFF2B2B2B);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setLineSpacing(0, 1.1f);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        titleParams.setMargins(0, dp(22), 0, dp(16));
        body.addView(title, titleParams);

        TextView desc = new TextView(this);
        desc.setText(item.description.isEmpty() ? "暂无正文。" : item.description);
        desc.setTextSize(18);
        desc.setTextColor(0xFF333333);
        desc.setLineSpacing(dp(5), 1.0f);
        body.addView(desc);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        actions.setPadding(dp(16), dp(8), dp(16), dp(10));
        actions.setBackgroundColor(0xFFFFFFFF);
        page.addView(actions, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(64)));

        TextView comment = new TextView(this);
        comment.setText("说点什么...");
        comment.setTextSize(15);
        comment.setTextColor(0xFF888888);
        comment.setGravity(Gravity.CENTER_VERTICAL);
        comment.setPadding(dp(16), 0, dp(16), 0);
        comment.setBackground(pillBackground(0xFFF4F4F4));
        actions.addView(comment, new LinearLayout.LayoutParams(0, dp(42), 1));

        actions.addView(detailAction(com.linkcollector.viewer.R.drawable.ic_antd_heart_outlined, fmt(item.liked)));
        actions.addView(detailTextAction("☆ " + fmt(item.collected)));
        actions.addView(detailTextAction("☻ " + fmt(item.comments)));
        return page;
    }

    private LinearLayout detailAction(int iconRes, String text) {
        LinearLayout action = new LinearLayout(this);
        action.setOrientation(LinearLayout.HORIZONTAL);
        action.setGravity(Gravity.CENTER);
        action.setPadding(dp(10), 0, 0, 0);
        ImageView icon = new ImageView(this);
        icon.setImageResource(iconRes);
        icon.setColorFilter(0xFF666666);
        action.addView(icon, new LinearLayout.LayoutParams(dp(22), dp(22)));
        TextView label = new TextView(this);
        label.setText(text);
        label.setTextSize(15);
        label.setTextColor(0xFF444444);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        labelParams.setMargins(dp(4), 0, 0, 0);
        action.addView(label, labelParams);
        return action;
    }

    private TextView detailTextAction(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(15);
        view.setTextColor(0xFF444444);
        view.setGravity(Gravity.CENTER);
        view.setSingleLine(true);
        view.setPadding(dp(10), 0, 0, 0);
        return view;
    }

    private LinearLayout videoAction(int iconRes, String text) {
        LinearLayout action = new LinearLayout(this);
        action.setOrientation(LinearLayout.HORIZONTAL);
        action.setGravity(Gravity.CENTER);
        action.setPadding(dp(14), 0, 0, 0);
        ImageView icon = new ImageView(this);
        icon.setImageResource(iconRes);
        icon.setColorFilter(0xFFFFFFFF);
        action.addView(icon, new LinearLayout.LayoutParams(dp(32), dp(32)));
        TextView label = new TextView(this);
        label.setText(text);
        label.setTextSize(18);
        label.setTextColor(0xFFFFFFFF);
        label.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        labelParams.setMargins(dp(6), 0, 0, 0);
        action.addView(label, labelParams);
        return action;
    }

    private LinearLayout videoTextAction(String icon, String text) {
        LinearLayout action = new LinearLayout(this);
        action.setOrientation(LinearLayout.HORIZONTAL);
        action.setGravity(Gravity.CENTER);
        action.setPadding(dp(18), 0, 0, 0);
        TextView iconView = new TextView(this);
        iconView.setText(icon);
        iconView.setTextColor(0xFFFFFFFF);
        iconView.setTextSize(34);
        iconView.setGravity(Gravity.CENTER);
        iconView.setIncludeFontPadding(false);
        action.addView(iconView, new LinearLayout.LayoutParams(dp(34), dp(38)));
        TextView label = new TextView(this);
        label.setText(text);
        label.setTextSize(18);
        label.setTextColor(0xFFFFFFFF);
        label.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        labelParams.setMargins(dp(6), 0, 0, 0);
        action.addView(label, labelParams);
        return action;
    }

    private void closeDetail() {
        if (detailVideoView != null) {
            detailVideoView.stopPlayback();
            detailVideoView = null;
        }
        if (detailView != null) {
            root.removeView(detailView);
            detailView = null;
        }
        if ("home".equals(activeView)) {
            topNav.setVisibility(View.VISIBLE);
            bottomNav.setVisibility(View.VISIBLE);
        } else if ("collect".equals(activeView)) {
            bottomNav.setVisibility(View.VISIBLE);
        }
    }

    private void closeDetailToHome() {
        closeDetail();
        showHome();
    }

    private void refreshItems() {
        if (refreshingItems) return;
        refreshingItems = true;
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONObject response = getJson(baseUrl + "/api/items");
                JSONArray array = response.optJSONArray("items");
                List<FeedItem> next = new ArrayList<>();
                if (array != null) {
                    for (int i = 0; i < array.length(); i++) {
                        next.add(FeedItem.fromJson(array.optJSONObject(i)));
                    }
                }
                mainHandler.post(() -> {
                    items.clear();
                    items.addAll(next);
                    refreshingItems = false;
                    setLoading(false);
                    if ("home".equals(activeView)) renderHome();
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    refreshingItems = false;
                    setLoading(false);
                    if ("home".equals(activeView)) renderHome();
                    Toast.makeText(this, exception.getMessage() == null ? "刷新失败" : exception.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private List<FeedItem> filteredItems(String query, String filter) {
        String q = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        List<FeedItem> result = new ArrayList<>();
        for (FeedItem item : items) {
            if (!q.isEmpty()) {
                String hay = (item.title + " " + item.author + " " + item.description).toLowerCase(Locale.ROOT);
                if (!hay.contains(q)) continue;
            }
            if ("xhs".equals(filter) || "douyin".equals(filter)) {
                if (!filter.equals(item.platform)) continue;
            } else if ("image".equals(filter)) {
                if (item.isVideo) continue;
            } else if ("video".equals(filter)) {
                if (!item.isVideo) continue;
            }
            result.add(item);
        }
        return result;
    }

    private void submitCollect() {
        String text = collectSourceInput == null ? "" : collectSourceInput.getText().toString().trim();
        List<String> sources = extractCollectSources(text);
        if (sources.isEmpty()) {
            setCollectStatus(containsCollectUrl(text) ? "没有识别到可采集的小红书/抖音内容链接。" : "请先粘贴链接或分享文本。", true);
            return;
        }
        boolean downloadMedia = collectDownloadCheck == null || collectDownloadCheck.isChecked();
        setCollectStatus(sources.size() == 1 ? "正在提交采集任务..." : "识别到 " + sources.size() + " 条，正在批量提交...", false);
        executor.execute(() -> {
            List<CollectTask> nextTasks = new ArrayList<>();
            int failedCount = 0;
            String firstError = "";
            try {
                for (String source : sources) {
                    try {
                        JSONObject payload = new JSONObject();
                        payload.put("source", source);
                        payload.put("platform", "auto");
                        payload.put("downloadMedia", downloadMedia);
                        JSONObject response = postJson(baseUrl + "/api/collect", payload);
                        if (!response.optBoolean("ok")) {
                            throw new RuntimeException(response.optString("error", "采集失败"));
                        }
                        nextTasks.add(CollectTask.fromJson(response.optJSONObject("task")));
                    } catch (Exception itemException) {
                        failedCount++;
                        if (firstError.isEmpty() && itemException.getMessage() != null) {
                            firstError = itemException.getMessage();
                        }
                        nextTasks.add(CollectTask.failed(source));
                    }
                }
                int finalFailedCount = failedCount;
                String finalFirstError = firstError;
                mainHandler.post(() -> {
                    collectTasks.addAll(0, nextTasks);
                    collectSourceInput.setText("");
                    if (finalFailedCount == 0) {
                        setCollectStatus(sources.size() == 1 ? "已加入后台队列。" : "已加入 " + sources.size() + " 条后台队列。", false);
                    } else if (finalFailedCount < sources.size()) {
                        setCollectStatus("已提交 " + (sources.size() - finalFailedCount) + " 条，" + finalFailedCount + " 条失败。", true);
                    } else {
                        setCollectStatus(finalFirstError.isEmpty() ? "批量提交失败。" : finalFirstError, true);
                    }
                    renderCollectTasks();
                    if (finalFailedCount < sources.size()) {
                        pollCollectTasks();
                    }
                });
            } catch (Exception exception) {
                mainHandler.post(() -> setCollectStatus(exception.getMessage() == null ? "采集失败" : exception.getMessage(), true));
            }
        });
    }

    private List<String> extractCollectSources(String text) {
        List<String> sources = new ArrayList<>();
        if (text == null) return sources;
        String normalized = text.trim();
        if (normalized.isEmpty()) return sources;
        Matcher matcher = COLLECT_URL_PATTERN.matcher(normalized);
        boolean foundUrl = false;
        while (matcher.find()) {
            foundUrl = true;
            addCollectUrlSource(sources, matcher.group());
        }
        if (foundUrl) return sources;
        if (!sources.isEmpty()) return sources;
        String[] lines = normalized.split("\\r?\\n");
        for (String line : lines) {
            addCollectSource(sources, cleanCollectSource(line));
        }
        if (sources.isEmpty()) addCollectSource(sources, normalized);
        return sources;
    }

    private boolean containsCollectUrl(String text) {
        return text != null && COLLECT_URL_PATTERN.matcher(text).find();
    }

    private void addCollectSource(List<String> sources, String source) {
        if (source == null || source.trim().isEmpty()) return;
        String clean = source.trim();
        if (!sources.contains(clean)) sources.add(clean);
    }

    private void addCollectUrlSource(List<String> sources, String source) {
        String clean = cleanCollectSource(source);
        if (clean.isEmpty() || !isSupportedCollectUrl(clean)) return;
        addCollectSource(sources, clean);
    }

    private String cleanCollectSource(String source) {
        if (source == null) return "";
        String clean = source.trim();
        while (!clean.isEmpty() && ".,;，。；、）)]".contains(clean.substring(clean.length() - 1))) {
            clean = clean.substring(0, clean.length() - 1).trim();
        }
        return clean;
    }

    private boolean isSupportedCollectUrl(String source) {
        try {
            URI uri = new URI(source);
            String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
            return host.endsWith("xhslink.com")
                    || host.endsWith("xiaohongshu.com")
                    || host.endsWith("xhs.cn")
                    || host.endsWith("douyin.com")
                    || host.endsWith("iesdouyin.com");
        } catch (Exception ignored) {
            return false;
        }
    }

    private void pollCollectTasks() {
        if (collectPollRequestRunning) return;
        collectPollRequestRunning = true;
        executor.execute(() -> {
            try {
                JSONObject response = getJson(baseUrl + "/api/collect-tasks");
                JSONArray array = response.optJSONArray("tasks");
                List<CollectTask> next = new ArrayList<>();
                if (array != null) {
                    for (int i = 0; i < array.length(); i++) {
                        next.add(CollectTask.fromJson(array.optJSONObject(i)));
                    }
                }
                boolean hasActiveTask = hasActiveCollectTask(next);
                mainHandler.post(() -> {
                    collectTasks.clear();
                    collectTasks.addAll(next);
                    renderCollectTasks();
                    collectPollRequestRunning = false;
                    if (hasActiveTask) {
                        scheduleCollectTaskPoll();
                    } else {
                        mainHandler.removeCallbacks(collectPollRunnable);
                        collectPollScheduled = false;
                        refreshItems();
                    }
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    collectPollRequestRunning = false;
                    if ("collect".equals(activeView)) scheduleCollectTaskPoll();
                });
            }
        });
    }

    private boolean hasActiveCollectTask(List<CollectTask> tasks) {
        for (CollectTask task : tasks) {
            if (task.isActive()) return true;
        }
        return false;
    }

    private void scheduleCollectTaskPoll() {
        if (collectPollScheduled) return;
        collectPollScheduled = true;
        mainHandler.postDelayed(collectPollRunnable, COLLECT_POLL_INTERVAL_MS);
    }

    private void renderCollectTasks() {
        if (collectTaskList == null) return;
        List<CollectTask> visibleTasks = uniqueCollectTasks(collectTasks);
        if (collectTaskCountLabel != null) collectTaskCountLabel.setText(visibleTasks.size() + " 条");
        collectTaskList.removeAllViews();
        if (visibleTasks.isEmpty()) {
            LinearLayout empty = new LinearLayout(this);
            empty.setOrientation(LinearLayout.VERTICAL);
            empty.setPadding(dp(18), dp(18), dp(18), dp(18));
            empty.setBackground(cardBackground(10, 0xFFFFFFFF, 0x0F000000));
            collectTaskList.addView(empty, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

            TextView emptyTitle = new TextView(this);
            emptyTitle.setText("还没有采集任务");
            emptyTitle.setTextSize(16);
            emptyTitle.setTextColor(0xFF333836);
            emptyTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            empty.addView(emptyTitle);

            TextView emptyCopy = new TextView(this);
            emptyCopy.setText("粘贴分享口令后，这里会显示解析和下载进度。");
            emptyCopy.setTextSize(14);
            emptyCopy.setTextColor(0xFF8A928E);
            LinearLayout.LayoutParams emptyCopyParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            emptyCopyParams.setMargins(0, dp(8), 0, 0);
            empty.addView(emptyCopy, emptyCopyParams);
            return;
        }
        for (CollectTask task : visibleTasks) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.TOP);
            row.setPadding(dp(14), dp(14), dp(14), dp(14));
            row.setBackground(cardBackground(10, 0xFFFFFFFF, 0x0F000000));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            params.setMargins(0, 0, 0, dp(8));
            collectTaskList.addView(row, params);

            TextView dot = new TextView(this);
            dot.setText("•");
            dot.setTextSize(30);
            dot.setTextColor(("失败".equals(task.statusText) || "部分完成".equals(task.statusText)) ? 0xFFC72239 : 0xFF0F8B7B);
            dot.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
            row.addView(dot, new LinearLayout.LayoutParams(dp(22), LinearLayout.LayoutParams.WRAP_CONTENT));

            LinearLayout textGroup = new LinearLayout(this);
            textGroup.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams textGroupParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
            textGroupParams.setMargins(dp(8), 0, dp(8), 0);
            row.addView(textGroup, textGroupParams);

            TextView title = new TextView(this);
            title.setText(task.title);
            title.setTextSize(16);
            title.setTextColor(0xFF252A28);
            title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            title.setMaxLines(2);
            textGroup.addView(title);

            TextView meta = new TextView(this);
            meta.setText(platformName(task.platform));
            meta.setTextSize(13);
            meta.setTextColor(0xFF8A928E);
            LinearLayout.LayoutParams metaParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            metaParams.setMargins(0, dp(6), 0, 0);
            textGroup.addView(meta, metaParams);

            TextView status = collectPill(task.statusText, statusPillColor(task.statusText), statusTextColor(task.statusText));
            row.addView(status, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(30)));
        }
    }

    private List<CollectTask> uniqueCollectTasks(List<CollectTask> tasks) {
        List<CollectTask> unique = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (CollectTask task : tasks) {
            if (task == null) continue;
            String key = task.stableKey();
            if (key == null || key.isEmpty()) key = task.title;
            if (seen.add(key)) unique.add(task);
        }
        return unique;
    }

    private TextView collectPill(String text, int backgroundColor, int textColor) {
        TextView pill = new TextView(this);
        pill.setText(text);
        pill.setTextSize(12);
        pill.setTextColor(textColor);
        pill.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        pill.setGravity(Gravity.CENTER);
        pill.setSingleLine(true);
        pill.setPadding(dp(10), 0, dp(10), 0);
        pill.setBackground(pillBackground(backgroundColor));
        return pill;
    }

    private int statusPillColor(String statusText) {
        if ("失败".equals(statusText)) return 0xFFFFEEF1;
        if ("部分完成".equals(statusText)) return 0xFFFFF1D6;
        if ("已完成".equals(statusText)) return 0xFFEFF8F2;
        if ("采集中".equals(statusText)) return 0xFFFFF6E8;
        return 0xFFF1F3F2;
    }

    private int statusTextColor(String statusText) {
        if ("失败".equals(statusText)) return 0xFFC72239;
        if ("部分完成".equals(statusText)) return 0xFF9A5A00;
        if ("已完成".equals(statusText)) return 0xFF15804E;
        if ("采集中".equals(statusText)) return 0xFFB56A00;
        return 0xFF69716E;
    }

    private void collectSharedText(String text) {
        if (password == null || password.trim().isEmpty()) {
            pendingSharedText = text;
            showLogin();
            return;
        }
        showCollect();
        if (collectSourceInput != null) {
            collectSourceInput.setText(text);
        }
        Toast.makeText(this, "已填入分享内容，请确认后采集", Toast.LENGTH_SHORT).show();
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
        setLoginStatus("正在验证登录信息...", false);
        executor.execute(() -> {
            try {
                boolean ok = checkHealth(nextBaseUrl, nextUsername, nextPassword);
                if (!ok) throw new RuntimeException("登录失败，请检查账号、密码或服务器地址。");
                baseUrl = nextBaseUrl;
                username = nextUsername;
                password = nextPassword;
                preferences.edit()
                        .putString(KEY_BASE_URL, baseUrl)
                        .putString(KEY_USERNAME, username)
                        .putString(KEY_PASSWORD, password)
                        .apply();
                mainHandler.post(() -> {
                    Toast.makeText(this, "登录成功", Toast.LENGTH_SHORT).show();
                    checkForUpdateThenShowApp();
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    loginButton.setEnabled(true);
                    setLoginStatus(exception.getMessage() == null ? "登录失败。" : exception.getMessage(), true);
                });
            }
        });
    }

    private boolean loadSavedCredentials() {
        String savedPassword = preferences.getString(KEY_PASSWORD, "");
        baseUrl = normalizeBaseUrl(preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL));
        username = preferences.getString(KEY_USERNAME, DEFAULT_USERNAME);
        password = savedPassword == null ? "" : savedPassword;
        return !password.trim().isEmpty();
    }

    private void checkForUpdateThenShowApp() {
        executor.execute(() -> {
            UpdateInfo updateInfo = fetchUpdateInfo(baseUrl);
            if (updateInfo != null && updateInfo.forceUpdate) {
                mainHandler.post(() -> showForceUpdateDialog(updateInfo));
                return;
            }
            mainHandler.post(this::showApp);
        });
    }

    private void checkForUpdate(String targetBaseUrl, Runnable onAllowed) {
        executor.execute(() -> {
            UpdateInfo updateInfo = fetchUpdateInfo(targetBaseUrl);
            if (updateInfo != null && updateInfo.forceUpdate) {
                mainHandler.post(() -> showForceUpdateDialog(updateInfo));
                return;
            }
            if (onAllowed != null) mainHandler.post(onAllowed);
        });
    }

    private UpdateInfo fetchUpdateInfo(String targetBaseUrl) {
        try {
            URL url = new URL(normalizeBaseUrl(targetBaseUrl) + "/api/app-version");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(8000);
            connection.setRequestMethod("GET");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) return null;
            JSONObject response = new JSONObject(readAll(connection.getInputStream()));
            int minSupportedVersionCode = response.optInt("minSupportedVersionCode", 1);
            int latestVersionCode = response.optInt("latestVersionCode", minSupportedVersionCode);
            int installedVersionCode = currentVersionCode();
            UpdateInfo info = new UpdateInfo();
            info.forceUpdate = response.optBoolean("forceUpdate", true)
                    && (installedVersionCode < minSupportedVersionCode || installedVersionCode < latestVersionCode);
            info.latestVersionName = response.optString("latestVersionName", "最新版本");
            info.latestVersionCode = latestVersionCode;
            String fallbackUrl = normalizeBaseUrl(targetBaseUrl) + "/downloads/social-media-claw-debug.apk";
            info.downloadUrl = trustedDownloadUrl(targetBaseUrl, response.optString("apkUrl", response.optString("downloadUrl", fallbackUrl)), fallbackUrl);
            info.apkSha256 = response.optString("apkSha256", response.optString("sha256", ""));
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
        appShell.setVisibility(View.GONE);
        loginView.setVisibility(View.GONE);
        new AlertDialog.Builder(this)
                .setTitle(updateInfo.title)
                .setMessage(updateInfo.message + "\n\n最新版本：" + updateInfo.latestVersionName)
                .setCancelable(false)
                .setPositiveButton("立即更新", (dialog, which) -> downloadApkInApp(updateInfo))
                .show();
    }

    private void downloadApkInApp(UpdateInfo updateInfo) {
        try {
            if (!isTrustedDownloadUrl(baseUrl, updateInfo.downloadUrl)) {
                Toast.makeText(this, "更新地址不可信，请检查服务器配置", Toast.LENGTH_LONG).show();
                showForceUpdateDialog(updateInfo);
                return;
            }
            Toast.makeText(this, "开始下载更新包...", Toast.LENGTH_SHORT).show();
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
                    if (completedId != downloadId) return;
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
                    if (updateInfo.apkSha256 != null && !updateInfo.apkSha256.trim().isEmpty()) {
                        try {
                            String actualSha256 = sha256OfUri(apkUri);
                            if (!updateInfo.apkSha256.trim().equalsIgnoreCase(actualSha256)) {
                                Toast.makeText(MainActivity.this, "更新包校验失败，请重新下载", Toast.LENGTH_LONG).show();
                                showForceUpdateDialog(updateInfo);
                                return;
                            }
                        } catch (Exception exception) {
                            Toast.makeText(MainActivity.this, "更新包校验失败：" + exception.getMessage(), Toast.LENGTH_LONG).show();
                            showForceUpdateDialog(updateInfo);
                            return;
                        }
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

    private JSONObject getJson(String target) throws Exception {
        URL url = new URL(target);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(300000);
        connection.setRequestMethod("GET");
        if (username != null && password != null) {
            connection.setRequestProperty("Authorization", basicAuth(username, password));
        }
        return readJsonResponse(connection);
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
        return readJsonResponse(connection);
    }

    private JSONObject deleteJson(String target) throws Exception {
        URL url = new URL(target);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(300000);
        connection.setRequestMethod("DELETE");
        connection.setRequestProperty("Authorization", basicAuth(username, password));
        return readJsonResponse(connection);
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

    private void loadImageInto(String rawUrl, ImageView imageView, boolean circle) {
        if (rawUrl == null || rawUrl.trim().isEmpty()) return;
        String url = mediaUrl(rawUrl);
        String cacheKey = circle ? "circle:" + url : url;
        imageView.setTag(cacheKey);
        Bitmap cached = imageCache.get(cacheKey);
        if (cached != null) {
            imageView.setImageBitmap(cached);
            return;
        }
        executor.execute(() -> {
            try {
                HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(30000);
                connection.setRequestProperty("User-Agent", "Mozilla/5.0 LinkCollectorViewer/1.0");
                byte[] bytes = readBytes(connection.getInputStream());
                Bitmap bitmap = decodeBitmap(bytes);
                if (bitmap != null) {
                    if (!circle && bitmap.getHeight() > 0) {
                        imageRatioCache.put(url, bitmap.getWidth() / (float) bitmap.getHeight());
                    }
                    Bitmap displayBitmap = circle ? circleBitmap(bitmap) : roundedBitmap(bitmap, dp(3));
                    imageCache.put(cacheKey, displayBitmap);
                    mainHandler.post(() -> {
                        if (cacheKey.equals(imageView.getTag())) {
                            if (!circle) {
                                updateImageHeight(imageView, bitmap.getWidth() / (float) bitmap.getHeight());
                            }
                            imageView.setImageBitmap(displayBitmap);
                        }
                    });
                }
            } catch (Exception ignored) {
            }
        });
    }

    private Bitmap decodeBitmap(byte[] bytes) {
        if (bytes == null || bytes.length == 0) return null;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                ImageDecoder.Source source = ImageDecoder.createSource(bytes);
                return ImageDecoder.decodeBitmap(source, (decoder, info, src) -> decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE));
            }
        } catch (Exception ignored) {
        }
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
    }

    private byte[] readBytes(InputStream stream) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        try (InputStream input = stream) {
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
        }
        return output.toByteArray();
    }

    private void updateImageHeight(ImageView imageView, float widthToHeight) {
        if (widthToHeight <= 0) return;
        android.view.ViewGroup.LayoutParams rawParams = imageView.getLayoutParams();
        if (rawParams == null) return;
        int targetHeight = heightForRatio(widthToHeight);
        if (rawParams.height != targetHeight) {
            rawParams.height = targetHeight;
            imageView.setLayoutParams(rawParams);
        }
    }

    private Bitmap circleBitmap(Bitmap source) {
        int size = Math.min(source.getWidth(), source.getHeight());
        Bitmap output = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(output);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        Path path = new Path();
        path.addCircle(size / 2f, size / 2f, size / 2f, Path.Direction.CW);
        canvas.clipPath(path);
        Rect src = new Rect(
                (source.getWidth() - size) / 2,
                (source.getHeight() - size) / 2,
                (source.getWidth() + size) / 2,
                (source.getHeight() + size) / 2
        );
        canvas.drawBitmap(source, src, new Rect(0, 0, size, size), paint);
        return output;
    }

    private Bitmap roundedBitmap(Bitmap source, int radius) {
        Bitmap output = Bitmap.createBitmap(source.getWidth(), source.getHeight(), Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(output);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        Path path = new Path();
        path.addRoundRect(new RectF(0, 0, source.getWidth(), source.getHeight()), radius, radius, Path.Direction.CW);
        canvas.clipPath(path);
        canvas.drawBitmap(source, 0, 0, paint);
        return output;
    }

    private String mediaUrl(String value) {
        try {
            if (value.startsWith("http://") || value.startsWith("https://")) {
                return value;
            }
            URL base = new URL(normalizeBaseUrl(baseUrl) + "/viewer/");
            URL resolved = new URL(base, value);
            URI uri = new URI(
                    resolved.getProtocol(),
                    resolved.getUserInfo(),
                    resolved.getHost(),
                    resolved.getPort(),
                    resolved.getPath(),
                    resolved.getQuery(),
                    resolved.getRef()
            );
            return uri.toASCIIString();
        } catch (Exception exception) {
            return value;
        }
    }

    private void showAccountMenu() {
        new AlertDialog.Builder(this)
                .setTitle("账号")
                .setItems(new String[]{"切换服务器 / 退出登录"}, (dialog, which) -> logoutToLogin())
                .show();
    }

    private void logoutToLogin() {
        preferences.edit().remove(KEY_PASSWORD).apply();
        password = "";
        showLogin();
    }

    private void selectNativeTab(String active) {
        setNativeTabState(homeTab, "home".equals(active));
        setNativeTabState(collectTab, "collect".equals(active));
        setNativeTabState(chatTab, false);
        setNativeTabState(meTab, false);
    }

    private void selectTopFilter() {
        if (topTabLabels == null || topTabIndicators == null) return;
        for (int i = 0; i < HOME_FILTERS.length; i++) {
            boolean active = HOME_FILTERS[i].equals(activeHomeFilter);
            topTabLabels[i].setTextColor(active ? 0xFF222222 : 0xFF9A9A9A);
            topTabLabels[i].setTypeface(Typeface.DEFAULT, active ? Typeface.BOLD : Typeface.NORMAL);
            topTabIndicators[i].setVisibility(active ? View.VISIBLE : View.INVISIBLE);
        }
    }

    private void setNativeTabState(TextView tab, boolean active) {
        if (tab == null) return;
        tab.setTextColor(active ? 0xFF222222 : 0xFF8C8C8C);
        tab.setTypeface(Typeface.DEFAULT, active ? Typeface.BOLD : Typeface.NORMAL);
    }

    private void updateLoginButtonState() {
        if (loginButton == null || serverInput == null || usernameInput == null || passwordInput == null) return;
        boolean complete = !serverInput.getText().toString().trim().isEmpty()
                && !usernameInput.getText().toString().trim().isEmpty()
                && !passwordInput.getText().toString().isEmpty()
                && agreementCheckBox != null
                && agreementCheckBox.isChecked();
        loginButton.setEnabled(complete);
        loginButton.setBackgroundResource(complete
                ? com.linkcollector.viewer.R.drawable.login_button_enabled
                : com.linkcollector.viewer.R.drawable.login_button_disabled);
    }

    private void setLoginStatus(String message, boolean error) {
        loginStatus.setText(message);
        loginStatus.setTextColor(error ? 0xFFC72239 : 0xFF666666);
    }

    private void setCollectStatus(String message, boolean error) {
        if (collectStatus != null) {
            collectStatus.setText(message);
            collectStatus.setTextColor(error ? 0xFFC72239 : 0xFF666666);
        }
    }

    private void setLoading(boolean loading) {
        progressBar.setVisibility(loading ? View.VISIBLE : View.GONE);
    }

    private String extractSharedText(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return null;
        CharSequence value = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        return value == null ? null : value.toString();
    }

    private int currentVersionCode() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    private String basicAuth(String targetUsername, String targetPassword) {
        String raw = targetUsername + ":" + targetPassword;
        return "Basic " + Base64.encodeToString(raw.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line);
            }
        }
        return builder.toString();
    }

    private JSONObject readJsonResponse(HttpURLConnection connection) throws Exception {
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
        String text = readAll(stream);
        JSONObject json = text.isEmpty() ? new JSONObject() : new JSONObject(text);
        if (status < 200 || status >= 300) {
            throw new RuntimeException(json.optString("error", "请求失败：" + status));
        }
        return json;
    }

    private String trustedDownloadUrl(String targetBaseUrl, String candidate, String fallback) {
        return isTrustedDownloadUrl(targetBaseUrl, candidate) ? candidate : fallback;
    }

    private boolean isTrustedDownloadUrl(String targetBaseUrl, String candidate) {
        try {
            URL base = new URL(normalizeBaseUrl(targetBaseUrl));
            URL url = new URL(candidate);
            if (!"https".equalsIgnoreCase(url.getProtocol())
                    && !(url.getHost().equalsIgnoreCase(base.getHost()) && url.getPort() == base.getPort())) {
                return false;
            }
            return "https".equalsIgnoreCase(url.getProtocol()) || url.getHost().equalsIgnoreCase(base.getHost());
        } catch (Exception exception) {
            return false;
        }
    }

    private String sha256OfUri(Uri uri) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[8192];
        try (InputStream input = getContentResolver().openInputStream(uri)) {
            if (input == null) throw new RuntimeException("无法读取更新包");
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
        byte[] hash = digest.digest();
        StringBuilder builder = new StringBuilder(hash.length * 2);
        for (byte value : hash) {
            builder.append(String.format(Locale.US, "%02x", value & 0xff));
        }
        return builder.toString();
    }

    private String normalizeBaseUrl(String value) {
        String normalized = value == null ? "" : value.trim();
        if (!normalized.isEmpty() && !normalized.contains("://")) normalized = "http://" + normalized;
        while (normalized.endsWith("/")) normalized = normalized.substring(0, normalized.length() - 1);
        return normalized;
    }

    private String platformName(String value) {
        if ("xhs".equals(value)) return "小红书";
        if ("douyin".equals(value)) return "抖音";
        return "自动识别";
    }

    private String fmt(long value) {
        if (value >= 10000) {
            double wan = value / 10000.0;
            return String.format(Locale.CHINA, wan >= 10 ? "%.0f万" : "%.1f万", wan);
        }
        return String.valueOf(value);
    }

    private String formatDuration(int millis) {
        if (millis <= 0) return "--:--";
        int totalSeconds = millis / 1000;
        int minutes = totalSeconds / 60;
        int seconds = totalSeconds % 60;
        return String.format(Locale.CHINA, "%d:%02d", minutes, seconds);
    }

    private LinearLayout.LayoutParams matchWidth(int height) {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, height);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private int statusBarHeight() {
        int resourceId = getResources().getIdentifier("status_bar_height", "dimen", "android");
        return resourceId > 0 ? getResources().getDimensionPixelSize(resourceId) : 0;
    }

    private GradientDrawable cardBackground(int radius, int color, int strokeColor) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radius));
        drawable.setStroke(Math.max(1, dp(1)), strokeColor);
        return drawable;
    }

    private GradientDrawable pillBackground(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(999));
        return drawable;
    }

    private GradientDrawable circleBackground(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setShape(GradientDrawable.OVAL);
        drawable.setColor(color);
        return drawable;
    }

    private static class FeedItem {
        String title = "";
        String author = "";
        String avatar = "";
        String cover = "";
        String video = "";
        String description = "";
        String platform = "";
        String source = "";
        boolean isVideo;
        long liked;
        long collected;
        long comments;

        static FeedItem fromJson(JSONObject json) {
            FeedItem item = new FeedItem();
            if (json == null) return item;
            item.title = json.optString("title", json.optString("name", ""));
            item.author = json.optString("author", "");
            item.avatar = json.optString("avatar", "");
            item.cover = json.optString("cover", "");
            item.video = json.optString("video", "");
            item.description = json.optString("description", "");
            item.platform = json.optString("platform", "");
            item.source = json.optString("source", "");
            item.isVideo = json.optBoolean("isVideo", "video".equals(json.optString("contentType")));
            JSONObject primary = json.optJSONObject("primary");
            if (primary != null) {
                // Remote URLs are kept in primary only for traceability. The app must display local files.
            }
            item.isVideo = item.isVideo || !item.video.isEmpty();
            item.liked = json.optLong("liked", 0);
            item.collected = json.optLong("collected", 0);
            item.comments = json.optLong("comments", 0);
            return item;
        }

        boolean hasPlayableVideo() {
            return video != null && !video.trim().isEmpty();
        }

        String playableVideo() {
            if (video != null && !video.trim().isEmpty()) return video;
            return "";
        }
    }

    private static class CollectTask {
        String id = "";
        String source = "";
        String title = "采集任务";
        String platform = "auto";
        String status = "queued";
        String statusText = "等待中";

        static CollectTask failed(String source) {
            CollectTask task = new CollectTask();
            String title = source == null ? "" : source.replaceAll("\\s+", " ").trim();
            task.source = title;
            task.title = title.isEmpty() ? "采集任务" : title.substring(0, Math.min(28, title.length()));
            task.platform = "auto";
            task.status = "error";
            task.statusText = "失败";
            return task;
        }

        static CollectTask fromJson(JSONObject json) {
            CollectTask task = new CollectTask();
            if (json == null) return task;
            String source = json.optString("source", "采集任务").replaceAll("\\s+", " ").trim();
            task.id = json.optString("id", "");
            task.source = source;
            task.title = source.isEmpty() ? "采集任务" : source.substring(0, Math.min(28, source.length()));
            task.platform = json.optString("platform", "auto");
            task.status = json.optString("status", "queued");
            if ("queued".equals(task.status)) task.statusText = "排队中";
            else if ("running".equals(task.status)) task.statusText = "采集中";
            else if ("ok".equals(task.status)) task.statusText = "已完成";
            else if ("partial".equals(task.status)) task.statusText = "部分完成";
            else if ("error".equals(task.status)) task.statusText = "失败";
            else task.statusText = "等待中";
            return task;
        }

        boolean isActive() {
            return "queued".equals(status) || "running".equals(status);
        }

        String stableKey() {
            if (id != null && !id.isEmpty()) return id;
            return source == null ? title : source;
        }
    }

    private static class SearchHistoryItem {
        String id = "";
        String keyword = "";
        String platform = "all";
        String filterSummary = "";
        long createdAt;
        int resultCount;

        static SearchHistoryItem fromJson(JSONObject json) {
            SearchHistoryItem item = new SearchHistoryItem();
            if (json == null) return item;
            item.id = json.optString("id", "");
            item.keyword = json.optString("keyword", "");
            item.platform = json.optString("platform", "all");
            item.filterSummary = json.optString("filterSummary", "");
            item.createdAt = json.optLong("createdAt", 0);
            item.resultCount = json.optInt("resultCount", 0);
            return item;
        }

        String metaText() {
            String platformLabel = "all".equals(platform) ? "全部" : "xhs".equals(platform) ? "小红书" : "抖音";
            return platformLabel + " · " + resultCount + "条";
        }
    }

    private static class UpdateInfo {
        String title;
        String message;
        String latestVersionName;
        int latestVersionCode;
        String downloadUrl;
        String apkSha256;
        boolean forceUpdate;

        UpdateInfo() {
        }

        UpdateInfo(String title, String message, String latestVersionName, int latestVersionCode, String downloadUrl, boolean forceUpdate) {
            this.title = title;
            this.message = message;
            this.latestVersionName = latestVersionName;
            this.latestVersionCode = latestVersionCode;
            this.downloadUrl = downloadUrl;
            this.apkSha256 = "";
            this.forceUpdate = forceUpdate;
        }
    }
}
