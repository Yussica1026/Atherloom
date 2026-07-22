package app.atherloom.mobile;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.Context;
import android.content.ClipboardManager;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.ViewGroup;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;
import androidx.webkit.WebViewAssetLoader;
import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 41, AUDIO_PERMISSION = 42;
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest pendingPermission;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        webView = new WebView(this);
        webView.setLayoutParams(new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(webView);
        configureWebView();
        webView.loadUrl("https://appassets.androidplatform.net/assets/index.html?standalone=1");
    }

    private void configureWebView() {
        WebViewAssetLoader loader = new WebViewAssetLoader.Builder().addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this)).build();
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
        webView.addJavascriptInterface(new NativeBridge(this, webView), "AtherloomNative");
        webView.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) { return loader.shouldInterceptRequest(request.getUrl()); }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("appassets.androidplatform.net".equalsIgnoreCase(uri.getHost())) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, uri)); return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback; startActivityForResult(params.createIntent(), FILE_CHOOSER); return true;
            }
            @Override public void onPermissionRequest(PermissionRequest request) {
                pendingPermission = request;
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) request.grant(request.getResources());
                else requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION);
            }
            @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) { callback.invoke(origin, false, false); }
        });
    }

    static class NativeBridge {
        private final SharedPreferences secrets;
        private final Context context;
        private final WebView webView;
        NativeBridge(Context context, WebView webView) {
            this.context = context;
            this.webView = webView;
            try {
                MasterKey key = new MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
                secrets = EncryptedSharedPreferences.create(context, "atherloom_secrets", key,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
            } catch (Exception error) { throw new IllegalStateException("Cannot initialize secure storage", error); }
        }

        @JavascriptInterface public String saveProvider(String raw) {
            try {
                JSONObject provider = new JSONObject(raw);
                String id = provider.optString("id"); if (id.isEmpty()) id = java.util.UUID.randomUUID().toString();
                if (provider.optString("api_key").isEmpty()) { JSONObject existing = new JSONObject(secrets.getString("provider:" + id, "{}")); if (!existing.optString("api_key").isEmpty()) provider.put("api_key", existing.optString("api_key")); }
                provider.put("id", id); secrets.edit().putString("provider:" + id, provider.toString()).apply();
                boolean hasKey = !provider.optString("api_key").isEmpty();
                provider.remove("api_key"); provider.put("has_api_key", hasKey); return provider.toString();
            } catch (Exception error) { return failure(error); }
        }

        @JavascriptInterface public String listProviders() {
            JSONArray output = new JSONArray();
            try { for (String key : secrets.getAll().keySet()) if (key.startsWith("provider:")) {
                JSONObject item = new JSONObject(secrets.getString(key, "{}")); boolean hasKey = !item.optString("api_key").isEmpty(); item.remove("api_key"); item.put("has_api_key", hasKey); output.put(item);
            }} catch (Exception error) { return failure(error); }
            return output.toString();
        }

        @JavascriptInterface public String deleteProvider(String id) { secrets.edit().remove("provider:" + id).apply(); return "{\"ok\":true}"; }

        @JavascriptInterface public String getClipboard() {
            ClipboardManager clipboard = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clipboard == null || !clipboard.hasPrimaryClip() || clipboard.getPrimaryClip() == null || clipboard.getPrimaryClip().getItemCount() == 0) return "";
            CharSequence value = clipboard.getPrimaryClip().getItemAt(0).coerceToText(context);
            return value == null ? "" : value.toString();
        }

        @JavascriptInterface public void showNotice(String message) {
            new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(context, message, Toast.LENGTH_LONG).show());
        }

        @JavascriptInterface public String listModels(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject provider = new JSONObject(raw); String protocol = provider.optString("protocol", "openai");
                String base = provider.getString("base_url").replaceAll("/+$", "");
                connection = (HttpURLConnection)new URL(base + "/models").openConnection(); connection.setRequestMethod("GET"); connection.setConnectTimeout(25000); connection.setReadTimeout(30000);
                if (protocol.equals("anthropic")) { connection.setRequestProperty("x-api-key", provider.optString("api_key")); connection.setRequestProperty("anthropic-version", "2023-06-01"); }
                else connection.setRequestProperty("Authorization", "Bearer " + provider.optString("api_key"));
                JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                int status = connection.getResponseCode(); String response = read(status >= 400 ? connection.getErrorStream() : connection.getInputStream());
                if (status >= 400) throw new Exception("HTTP " + status + " · " + response.substring(0, Math.min(240, response.length())));
                JSONArray rows = new JSONObject(response).optJSONArray("data"), models = new JSONArray();
                if (rows != null) for (int i = 0; i < rows.length(); i++) { Object row = rows.get(i); String id = row instanceof JSONObject ? ((JSONObject)row).optString("id") : row instanceof String ? (String)row : ""; if (!id.isEmpty()) models.put(id); }
                return models.toString();
            } catch (Exception error) { return failure(error); } finally { if (connection != null) connection.disconnect(); }
        }

        @JavascriptInterface public String chat(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject request = new JSONObject(raw);
                JSONObject provider = new JSONObject(secrets.getString("provider:" + request.getString("provider_id"), "{}"));
                if (!provider.has("base_url")) throw new Exception("API 线路不存在");
                String protocol = provider.optString("protocol", "openai");
                String base = provider.getString("base_url").replaceAll("/+$", "");
                String endpoint = protocol.equals("anthropic") ? (base.endsWith("/v1") ? base + "/messages" : base + "/v1/messages") : (base.endsWith("/chat/completions") ? base : base + "/chat/completions");
                JSONArray requestMessages = request.getJSONArray("messages");
                if (!protocol.equals("anthropic") && !request.optString("system").isEmpty()) { JSONArray withSystem = new JSONArray(); withSystem.put(new JSONObject().put("role", "system").put("content", request.getString("system"))); for (int i=0;i<requestMessages.length();i++) withSystem.put(requestMessages.get(i)); requestMessages=withSystem; }
                JSONObject payload = new JSONObject(); payload.put("model", provider.getString("model")); payload.put("max_tokens", provider.optInt("max_tokens", 4096)); payload.put("temperature", provider.optDouble("temperature", 0.7)); payload.put("top_p", provider.optDouble("top_p", 1.0)); payload.put("messages", requestMessages);
                if (protocol.equals("anthropic") && request.has("system")) payload.put("system", request.getString("system"));
                if ((protocol.equals("deepseek") || protocol.equals("glm")) && provider.optBoolean("thinking_enabled", true)) payload.put("thinking", new JSONObject().put("type", "enabled"));
                connection = (HttpURLConnection)new URL(endpoint).openConnection(); connection.setRequestMethod("POST"); connection.setConnectTimeout(25000); connection.setReadTimeout(180000); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
                String apiKey = provider.optString("api_key");
                if (protocol.equals("anthropic")) { connection.setRequestProperty("x-api-key", apiKey); connection.setRequestProperty("anthropic-version", "2023-06-01"); }
                else connection.setRequestProperty("Authorization", "Bearer " + apiKey);
                JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                try (OutputStream output = connection.getOutputStream()) { output.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
                int status = connection.getResponseCode(); String response = read(status >= 400 ? connection.getErrorStream() : connection.getInputStream());
                if (status >= 400) throw new Exception("HTTP " + status + " · " + response.substring(0, Math.min(300, response.length())));
                JSONObject data = new JSONObject(response); String content;
                if (protocol.equals("anthropic")) { StringBuilder text = new StringBuilder(); JSONArray blocks=data.optJSONArray("content"); if(blocks!=null)for(int i=0;i<blocks.length();i++)if("text".equals(blocks.getJSONObject(i).optString("type")))text.append(blocks.getJSONObject(i).optString("text")); content=text.toString(); }
                else content=nullableString(data.getJSONArray("choices").getJSONObject(0).getJSONObject("message"), "content");
                JSONObject responseMessage = protocol.equals("anthropic") ? null : data.getJSONArray("choices").getJSONObject(0).getJSONObject("message");
                String reasoning = protocol.equals("anthropic") ? "" : nullableString(responseMessage, "reasoning_content"); if (reasoning.isEmpty()) reasoning=nullableString(responseMessage, "reasoning");
                return new JSONObject().put("ok", true).put("content", content).put("reasoning", reasoning).put("model", provider.optString("model")).toString();
            } catch (Exception error) { return failure(error); } finally { if (connection != null) connection.disconnect(); }
        }

        @JavascriptInterface public void chatStream(String raw, String callbackId) {
            new Thread(() -> {
                HttpURLConnection connection = null;
                try {
                    JSONObject request = new JSONObject(raw);
                    JSONObject provider = new JSONObject(secrets.getString("provider:" + request.getString("provider_id"), "{}"));
                    if (!provider.has("base_url")) throw new Exception("API 线路不存在");
                    String protocol = provider.optString("protocol", "openai");
                    String base = provider.getString("base_url").replaceAll("/+$", "");
                    String endpoint = protocol.equals("anthropic") ? (base.endsWith("/v1") ? base + "/messages" : base + "/v1/messages") : (base.endsWith("/chat/completions") ? base : base + "/chat/completions");
                    JSONArray requestMessages = request.getJSONArray("messages");
                    if (!protocol.equals("anthropic") && !request.optString("system").isEmpty()) { JSONArray withSystem = new JSONArray(); withSystem.put(new JSONObject().put("role", "system").put("content", request.getString("system"))); for (int i=0;i<requestMessages.length();i++) withSystem.put(requestMessages.get(i)); requestMessages=withSystem; }
                    JSONObject payload = new JSONObject(); payload.put("model", provider.getString("model")); payload.put("max_tokens", provider.optInt("max_tokens", 4096)); payload.put("temperature", provider.optDouble("temperature", 0.7)); payload.put("top_p", provider.optDouble("top_p", 1.0)); payload.put("stream", true); payload.put("messages", requestMessages);
                    if (protocol.equals("anthropic") && request.has("system")) payload.put("system", request.getString("system"));
                    if ((protocol.equals("deepseek") || protocol.equals("glm")) && provider.optBoolean("thinking_enabled", true)) payload.put("thinking", new JSONObject().put("type", "enabled"));
                    connection = (HttpURLConnection)new URL(endpoint).openConnection(); connection.setRequestMethod("POST"); connection.setConnectTimeout(25000); connection.setReadTimeout(180000); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
                    String apiKey = provider.optString("api_key");
                    if (protocol.equals("anthropic")) { connection.setRequestProperty("x-api-key", apiKey); connection.setRequestProperty("anthropic-version", "2023-06-01"); }
                    else connection.setRequestProperty("Authorization", "Bearer " + apiKey);
                    JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                    for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                    try (OutputStream output = connection.getOutputStream()) { output.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
                    int status = connection.getResponseCode();
                    if (status >= 400) { String response = read(connection.getErrorStream()); throw new Exception("HTTP " + status + " · " + response.substring(0, Math.min(300, response.length()))); }
                    try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                        String line;
                        while ((line = reader.readLine()) != null) {
                            if (!line.startsWith("data:")) continue;
                            String rawEvent = line.substring(5).trim(); if (rawEvent.isEmpty() || rawEvent.equals("[DONE]")) continue;
                            JSONObject event = new JSONObject(rawEvent), output = new JSONObject();
                            if (protocol.equals("anthropic")) {
                                JSONObject delta = event.optJSONObject("delta");
                                if (delta != null && event.optString("type").equals("content_block_delta")) { if (!delta.optString("text").isEmpty()) output.put("delta", delta.optString("text")); if (!delta.optString("thinking").isEmpty()) output.put("reasoning_delta", delta.optString("thinking")); }
                            } else {
                                JSONArray choices = event.optJSONArray("choices"); JSONObject delta = choices != null && choices.length() > 0 ? choices.getJSONObject(0).optJSONObject("delta") : null;
                                if (delta != null) { String content=nullableString(delta,"content"); if (!content.isEmpty()) output.put("delta",content); String reasoning=nullableString(delta,"reasoning_content"); if(reasoning.isEmpty())reasoning=nullableString(delta,"reasoning"); if (!reasoning.isEmpty()) output.put("reasoning_delta", reasoning); }
                            }
                            if (output.length() > 0) emitStream(callbackId, output);
                        }
                    }
                    emitStream(callbackId, new JSONObject().put("done", true).put("model", provider.optString("model")));
                } catch (Exception error) {
                    try { emitStream(callbackId, new JSONObject().put("error", error.getMessage())); } catch (Exception ignored) {}
                } finally { if (connection != null) connection.disconnect(); }
            }).start();
        }

        private void emitStream(String callbackId, JSONObject event) {
            String script = "window.AtherloomNativeStream&&window.AtherloomNativeStream(" + JSONObject.quote(callbackId) + "," + JSONObject.quote(event.toString()) + ")";
            webView.post(() -> webView.evaluateJavascript(script, null));
        }

        private static String nullableString(JSONObject object, String key) {
            return object == null || !object.has(key) || object.isNull(key) ? "" : object.optString(key, "");
        }

        private static String read(InputStream stream) throws Exception { if(stream==null)return ""; StringBuilder text=new StringBuilder(); try(BufferedReader reader=new BufferedReader(new InputStreamReader(stream,StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null)text.append(line);} return text.toString(); }
        private static String failure(Exception error) { try { return new JSONObject().put("ok",false).put("error",error.getMessage()).toString(); } catch(Exception ignored){ return "{\"ok\":false,\"error\":\"unknown\"}"; } }
    }

    @Override public void onBackPressed() {
        webView.evaluateJavascript("window.AtherloomHandleBack ? window.AtherloomHandleBack() : false", handled -> {
            if (!"true".equals(handled)) { if (webView.canGoBack()) webView.goBack(); else MainActivity.super.onBackPressed(); }
        });
    }
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
