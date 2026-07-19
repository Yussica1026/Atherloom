package app.atherloom.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.Menu;
import android.view.MenuItem;
import android.view.ViewGroup;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 41;
    private static final int AUDIO_PERMISSION = 42;
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest pendingPermission;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        webView = new WebView(this);
        webView.setLayoutParams(new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(webView);
        configureWebView();
        webView.setOnLongClickListener(view -> { showServerDialog(); return true; });
        String server = getPreferences(MODE_PRIVATE).getString("server_url", "");
        if (server.isEmpty()) showServerDialog(); else webView.loadUrl(server);
    }

    private void configureWebView() {
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                Uri home = Uri.parse(getPreferences(MODE_PRIVATE).getString("server_url", ""));
                if (home.getHost() != null && home.getHost().equalsIgnoreCase(uri.getHost())) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, uri)); return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                startActivityForResult(params.createIntent(), FILE_CHOOSER); return true;
            }
            @Override public void onPermissionRequest(PermissionRequest request) {
                pendingPermission = request;
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) request.grant(request.getResources());
                else requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION);
            }
            @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) { callback.invoke(origin, false, false); }
        });
    }

    private void showServerDialog() {
        EditText input = new EditText(this); input.setHint("https://your-atherloom.example.com"); input.setSingleLine(true);
        new AlertDialog.Builder(this).setTitle("连接 Atherloom").setMessage("填写你的 Atherloom 后端地址。局域网 HTTP 可用于测试，长期使用建议 HTTPS。")
            .setView(input).setCancelable(false).setPositiveButton("连接", (dialog, which) -> {
                String value = input.getText().toString().trim();
                if (!value.startsWith("http://") && !value.startsWith("https://")) value = "https://" + value;
                getPreferences(MODE_PRIVATE).edit().putString("server_url", value).apply(); webView.loadUrl(value);
            }).show();
    }

    @Override public boolean onCreateOptionsMenu(Menu menu) { menu.add("更换服务器").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER); return true; }
    @Override public boolean onOptionsItemSelected(MenuItem item) { showServerDialog(); return true; }
    @Override public void onBackPressed() { if (webView.canGoBack()) webView.goBack(); else super.onBackPressed(); }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == FILE_CHOOSER && fileCallback != null) { fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data)); fileCallback = null; }
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == AUDIO_PERMISSION && pendingPermission != null) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) pendingPermission.grant(pendingPermission.getResources());
            else { pendingPermission.deny(); Toast.makeText(this, "未授予麦克风权限", Toast.LENGTH_SHORT).show(); }
            pendingPermission = null;
        }
    }
}
