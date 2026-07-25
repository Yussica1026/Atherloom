package app.atherloom.mobile;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.Context;
import android.content.ContentValues;
import android.content.ClipboardManager;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
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
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 41, AUDIO_PERMISSION = 42;
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private Uri pendingCameraUri;
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
                fileCallback = callback;
                boolean imageCapture = params.isCaptureEnabled();
                String[] accepted = params.getAcceptTypes();
                if (accepted != null) for (String type : accepted) if (type != null && type.startsWith("image/")) imageCapture = imageCapture || params.isCaptureEnabled();
                if (imageCapture) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Images.Media.DISPLAY_NAME, "atherloom-" + System.currentTimeMillis() + ".jpg");
                    values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
                    pendingCameraUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
                    Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                    camera.putExtra(MediaStore.EXTRA_OUTPUT, pendingCameraUri);
                    startActivityForResult(camera, FILE_CHOOSER);
                } else {
                    pendingCameraUri = null;
                    startActivityForResult(params.createIntent(), FILE_CHOOSER);
                }
                return true;
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

        @JavascriptInterface public String webSearch(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject request=new JSONObject(raw);String query=request.optString("query").trim();int limit=Math.max(1,Math.min(request.optInt("max_results",5),8));
                if(query.isEmpty())throw new Exception("搜索关键词不能为空");
                String endpoint="https://api.duckduckgo.com/?q="+URLEncoder.encode(query,"UTF-8")+"&format=json&no_html=1&skip_disambig=1";
                connection=(HttpURLConnection)new URL(endpoint).openConnection();connection.setConnectTimeout(20000);connection.setReadTimeout(25000);connection.setRequestProperty("User-Agent","Atherloom/0.5 Android");
                int status=connection.getResponseCode();String response=read(status>=400?connection.getErrorStream():connection.getInputStream());if(status>=400)throw new Exception("搜索服务 HTTP "+status);
                JSONObject data=new JSONObject(response),output=new JSONObject().put("query",query);JSONArray results=new JSONArray();
                if(!data.optString("AbstractURL").isEmpty())results.put(new JSONObject().put("title",data.optString("Heading",query)).put("url",data.optString("AbstractURL")).put("snippet",data.optString("AbstractText")));
                JSONArray related=data.optJSONArray("RelatedTopics");if(related!=null)for(int i=0;i<related.length()&&results.length()<limit;i++){JSONObject row=related.optJSONObject(i);if(row==null)continue;if(row.has("Topics")){JSONArray nested=row.optJSONArray("Topics");row=nested!=null&&nested.length()>0?nested.optJSONObject(0):null;}if(row!=null&&!row.optString("FirstURL").isEmpty())results.put(new JSONObject().put("title",row.optString("Text").split(" - ")[0]).put("url",row.optString("FirstURL")).put("snippet",row.optString("Text")));}
                return output.put("results",results).put("result_count",results.length()).toString();
            } catch(Exception error){return failure(error);} finally {if(connection!=null)connection.disconnect();}
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
                JSONObject payload = new JSONObject(); payload.put("model", provider.getString("model")); payload.put("max_tokens", request.optInt("max_tokens", provider.optInt("max_tokens", 4096))); payload.put("temperature", request.optDouble("temperature", provider.optDouble("temperature", 0.7))); payload.put("top_p", request.optDouble("top_p", provider.optDouble("top_p", 1.0))); payload.put("messages", requestMessages);
                if (protocol.equals("anthropic") && request.has("system")) payload.put("system", request.getString("system"));
                if (request.has("tools")) {
                    JSONArray requested = request.getJSONArray("tools"), tools = new JSONArray();
                    for (int i=0;i<requested.length();i++) {
                        JSONObject tool=requested.getJSONObject(i);
                        tools.put(protocol.equals("anthropic") ? new JSONObject().put("name",tool.getString("name")).put("description",tool.optString("description")).put("input_schema",tool.getJSONObject("input_schema")) : new JSONObject().put("type","function").put("function",new JSONObject().put("name",tool.getString("name")).put("description",tool.optString("description")).put("parameters",tool.getJSONObject("input_schema"))));
                    }
                    payload.put("tools",tools);
                }
                if ((protocol.equals("deepseek") || protocol.equals("glm")) && request.optBoolean("thinking_enabled", provider.optBoolean("thinking_enabled", true))) payload.put("thinking", new JSONObject().put("type", "enabled"));
                connection = (HttpURLConnection)new URL(endpoint).openConnection(); connection.setRequestMethod("POST"); connection.setConnectTimeout(25000); connection.setReadTimeout(180000); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
                String apiKey = provider.optString("api_key");
                if (protocol.equals("anthropic")) { connection.setRequestProperty("x-api-key", apiKey); connection.setRequestProperty("anthropic-version", "2023-06-01"); }
                else connection.setRequestProperty("Authorization", "Bearer " + apiKey);
                JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                try (OutputStream output = connection.getOutputStream()) { output.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
                int status = connection.getResponseCode(); String response = read(status >= 400 ? connection.getErrorStream() : connection.getInputStream());
                if (status >= 400) throw new Exception("HTTP " + status + " · " + response.substring(0, Math.min(300, response.length())));
                JSONObject data = new JSONObject(response); String content; JSONArray toolCalls=new JSONArray(); Object rawAssistant;
                if (protocol.equals("anthropic")) { StringBuilder text = new StringBuilder(); JSONArray blocks=data.optJSONArray("content"); rawAssistant=blocks==null?new JSONArray():blocks;if(blocks!=null)for(int i=0;i<blocks.length();i++){JSONObject block=blocks.getJSONObject(i);if("text".equals(block.optString("type")))text.append(block.optString("text"));if("tool_use".equals(block.optString("type")))toolCalls.put(new JSONObject().put("id",block.optString("id")).put("name",block.optString("name")).put("arguments",block.optJSONObject("input")==null?new JSONObject():block.optJSONObject("input")));} content=text.toString(); }
                else {JSONObject message=data.getJSONArray("choices").getJSONObject(0).getJSONObject("message");rawAssistant=message;content=nullableString(message, "content");JSONArray calls=message.optJSONArray("tool_calls");if(calls!=null)for(int i=0;i<calls.length();i++){JSONObject call=calls.getJSONObject(i),function=call.optJSONObject("function");if(function!=null)toolCalls.put(new JSONObject().put("id",call.optString("id")).put("name",function.optString("name")).put("arguments",new JSONObject(function.optString("arguments","{}"))));}}
                JSONObject responseMessage = protocol.equals("anthropic") ? null : data.getJSONArray("choices").getJSONObject(0).getJSONObject("message");
                String reasoning = protocol.equals("anthropic") ? "" : nullableString(responseMessage, "reasoning_content"); if (reasoning.isEmpty()) reasoning=nullableString(responseMessage, "reasoning");
                return new JSONObject().put("ok", true).put("content", content).put("reasoning", reasoning).put("model", provider.optString("model")).put("tool_calls",toolCalls).put("raw_assistant",rawAssistant).toString();
            } catch (Exception error) { return failure(error); } finally { if (connection != null) connection.disconnect(); }
        }

        @JavascriptInterface public void chatAsync(String raw, String callbackId) {
            new Thread(() -> {
                String result = chat(raw);
                webView.post(() -> webView.evaluateJavascript("window.AtherloomNativeResolve(" + JSONObject.quote(callbackId) + "," + JSONObject.quote(result) + ")", null));
            }).start();
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
        if (request == FILE_CHOOSER && fileCallback != null) {
            Uri[] resultUris = result == RESULT_OK && pendingCameraUri != null ? new Uri[]{pendingCameraUri} : WebChromeClient.FileChooserParams.parseResult(result, data);
            if (result != RESULT_OK && pendingCameraUri != null) getContentResolver().delete(pendingCameraUri, null, null);
            fileCallback.onReceiveValue(resultUris);
            fileCallback = null;
            pendingCameraUri = null;
        }
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
