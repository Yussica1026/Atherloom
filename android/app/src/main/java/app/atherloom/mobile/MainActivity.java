package app.atherloom.mobile;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.Context;
import android.content.ContentValues;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Build;
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
import org.json.JSONTokener;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.lang.ref.WeakReference;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 41, AUDIO_PERMISSION = 42, NOTIFICATION_PERMISSION = 43;
    private static WeakReference<WebView> liveWebView = new WeakReference<>(null);
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private Uri pendingCameraUri;
    private PermissionRequest pendingPermission;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        webView = new WebView(this);
        liveWebView = new WeakReference<>(webView);
        webView.setLayoutParams(new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(webView);
        configureWebView();
        webView.loadUrl("https://appassets.androidplatform.net/assets/index.html?standalone=1");
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_PERMISSION);
    }

    @Override protected void onResume() {
        super.onResume();
        if (webView == null) return;
        webView.onResume();
        webView.resumeTimers();
        webView.post(() -> webView.evaluateJavascript("window.AtherloomResumeParlor&&window.AtherloomResumeParlor()", null));
    }

    static boolean runAutonomyWake() {
        WebView view = liveWebView.get();
        if (view == null) return false;
        view.post(() -> view.evaluateJavascript("window.AtherloomRunAutonomyWake&&window.AtherloomRunAutonomyWake()", null));
        return true;
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
                if (provider.optString("api_key").isEmpty()) {
                    JSONObject existing = new JSONObject(secrets.getString("provider:" + id, "{}"));
                    if (existing.optString("api_key").isEmpty() && !provider.optString("source_provider_id").isEmpty())
                        existing = new JSONObject(secrets.getString("provider:" + provider.optString("source_provider_id"), "{}"));
                    if (existing.optString("api_key").isEmpty()) {
                        for (String keyName : secrets.getAll().keySet()) if (keyName.startsWith("provider:")) {
                            JSONObject candidate = new JSONObject(secrets.getString(keyName, "{}"));
                            if (candidate.optString("protocol").equals(provider.optString("protocol"))
                                && candidate.optString("base_url").replaceAll("/+$", "").equals(provider.optString("base_url").replaceAll("/+$", ""))
                                && !candidate.optString("api_key").isEmpty()) { existing = candidate; break; }
                        }
                    }
                    if (!existing.optString("api_key").isEmpty()) provider.put("api_key", existing.optString("api_key"));
                }
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

        @JavascriptInterface public String saveMcpServer(String raw) {
            try {
                JSONObject server=new JSONObject(raw);String id=server.optString("id");if(id.isEmpty())id=java.util.UUID.randomUUID().toString();
                JSONObject existing=new JSONObject(secrets.getString("mcp:"+id,"{}"));
                if(server.optString("token").isEmpty()&&!existing.optString("token").isEmpty())server.put("token",existing.optString("token"));
                server.put("id",id);secrets.edit().putString("mcp:"+id,server.toString()).apply();
                boolean hasToken=!server.optString("token").isEmpty();server.remove("token");server.put("has_token",hasToken);return server.toString();
            }catch(Exception error){return failure(error);}
        }

        @JavascriptInterface public String listMcpServers() {
            JSONArray output=new JSONArray();
            try{for(String key:secrets.getAll().keySet())if(key.startsWith("mcp:")){JSONObject item=new JSONObject(secrets.getString(key,"{}"));boolean hasToken=!item.optString("token").isEmpty();item.remove("token");item.put("has_token",hasToken);output.put(item);}}catch(Exception error){return failure(error);}
            return output.toString();
        }

        @JavascriptInterface public String deleteMcpServer(String id){secrets.edit().remove("mcp:"+id).apply();return "{\"ok\":true}";}

        @JavascriptInterface public String mcpRequest(String raw) {
            HttpURLConnection connection=null;
            try{
                JSONObject request=new JSONObject(raw),server=new JSONObject(secrets.getString("mcp:"+request.getString("server_id"),"{}"));
                if(server.length()==0)throw new Exception("MCP 服务不存在");
                String endpoint=server.optString("url");if(!endpoint.startsWith("https://")&&!endpoint.startsWith("http://"))throw new Exception("MCP 地址必须使用 HTTP 或 HTTPS");
                connection=(HttpURLConnection)new URL(endpoint).openConnection();connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setConnectTimeout(25000);connection.setReadTimeout(60000);connection.setRequestProperty("Content-Type","application/json");connection.setRequestProperty("Accept","application/json, text/event-stream");
                String token=server.optString("token");if(!token.isEmpty())connection.setRequestProperty("Authorization","Bearer "+token);
                String session=request.optString("session_id");if(!session.isEmpty())connection.setRequestProperty("Mcp-Session-Id",session);
                JSONObject headers=server.optJSONObject("headers");if(headers!=null)for(Iterator<String> keys=headers.keys();keys.hasNext();){String name=keys.next();connection.setRequestProperty(name,headers.optString(name));}
                try(OutputStream output=connection.getOutputStream()){output.write(request.getJSONObject("payload").toString().getBytes(StandardCharsets.UTF_8));}
                int status=connection.getResponseCode();String text=read(status>=400?connection.getErrorStream():connection.getInputStream());if(status>=400)throw new Exception("MCP HTTP "+status+" · "+text.substring(0,Math.min(300,text.length())));
                if(text.startsWith("event:")||text.startsWith("data:")){String latest="";for(String line:text.split("\\r?\\n"))if(line.startsWith("data:")&&!line.substring(5).trim().isEmpty())latest=line.substring(5).trim();text=latest;}
                JSONObject result=text.isEmpty()?new JSONObject():new JSONObject(text);return new JSONObject().put("ok",true).put("response",result).put("session_id",connection.getHeaderField("Mcp-Session-Id")==null?session:connection.getHeaderField("Mcp-Session-Id")).toString();
            }catch(Exception error){return failure(error);}finally{if(connection!=null)connection.disconnect();}
        }

        @JavascriptInterface public String getClipboard() {
            ClipboardManager clipboard = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clipboard == null || !clipboard.hasPrimaryClip() || clipboard.getPrimaryClip() == null || clipboard.getPrimaryClip().getItemCount() == 0) return "";
            CharSequence value = clipboard.getPrimaryClip().getItemAt(0).coerceToText(context);
            return value == null ? "" : value.toString();
        }

        @JavascriptInterface public boolean setClipboard(String value) {
            ClipboardManager clipboard = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clipboard == null) return false;
            clipboard.setPrimaryClip(ClipData.newPlainText("Atherloom 邀请码", value == null ? "" : value));
            return true;
        }

        @JavascriptInterface public String readBundledAsset(String path) {
            if (!"assets/nowhere/index.html".equals(path)) return "";
            try { return read(context.getAssets().open("assets/nowhere/index.html")); }
            catch (Exception error) { return ""; }
        }

        @JavascriptInterface public void showNotice(String message) {
            new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(context, message, Toast.LENGTH_LONG).show());
        }

        @JavascriptInterface public String configureAutonomy(String raw) {
            try {
                JSONObject config = new JSONObject(raw);
                context.getSharedPreferences("atherloom_runtime", Context.MODE_PRIVATE).edit().putString("autonomy_config", config.toString()).apply();
                Intent intent = new Intent(context, AutonomyService.class).setAction(config.optBoolean("enabled") ? AutonomyService.ACTION_START : AutonomyService.ACTION_STOP);
                if (config.optBoolean("enabled") && Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent); else context.startService(intent);
                return "{\"ok\":true}";
            } catch (Exception error) { return failure(error); }
        }

        @JavascriptInterface public String listModels(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject provider = new JSONObject(raw); String protocol = provider.optString("protocol", "openai");
                if (provider.optString("api_key").isEmpty() && !provider.optString("provider_id").isEmpty()) {
                    JSONObject saved = new JSONObject(secrets.getString("provider:" + provider.optString("provider_id"), "{}"));
                    if (!saved.optString("api_key").isEmpty()) provider.put("api_key", saved.optString("api_key"));
                }
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

        @JavascriptInterface public String embed(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject request = new JSONObject(raw);
                JSONObject provider = new JSONObject(secrets.getString("provider:" + request.getString("provider_id"), "{}"));
                if (!provider.has("base_url")) throw new Exception("向量线路不存在");
                if (provider.optString("protocol", "openai").equals("anthropic")) throw new Exception("Anthropic 原生线路不提供向量接口");
                String base = provider.getString("base_url").replaceAll("/+$", "").replaceAll("/chat/completions$", "");
                String endpoint = base.endsWith("/embeddings") ? base : base + "/embeddings";
                connection = (HttpURLConnection)new URL(endpoint).openConnection();
                connection.setRequestMethod("POST"); connection.setDoOutput(true);
                connection.setConnectTimeout(25000); connection.setReadTimeout(60000);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setRequestProperty("Authorization", "Bearer " + provider.optString("api_key"));
                JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                JSONObject payload = new JSONObject().put("model", request.getString("model")).put("input", request.getJSONArray("texts"));
                try (OutputStream output = connection.getOutputStream()) { output.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
                int status = connection.getResponseCode(); String response = read(status >= 400 ? connection.getErrorStream() : connection.getInputStream());
                if (status >= 400) throw new Exception("向量服务 HTTP " + status + " · " + response.substring(0, Math.min(240, response.length())));
                JSONArray rows = new JSONObject(response).optJSONArray("data"), vectors = new JSONArray();
                if (rows == null || rows.length() != request.getJSONArray("texts").length()) throw new Exception("向量返回数量不一致");
                JSONObject[] ordered = new JSONObject[rows.length()];
                for (int i = 0; i < rows.length(); i++) { JSONObject row = rows.getJSONObject(i); int index = row.optInt("index", i); if (index < 0 || index >= ordered.length) throw new Exception("向量索引无效"); ordered[index] = row; }
                for (JSONObject row : ordered) {
                    if (row == null) throw new Exception("向量索引不完整");
                    JSONArray source = row.getJSONArray("embedding"), normalized = new JSONArray(); double magnitude = 0;
                    for (int i = 0; i < source.length(); i++) { double value = source.getDouble(i); magnitude += value * value; }
                    magnitude = Math.sqrt(magnitude); if (magnitude <= 0) throw new Exception("向量服务返回了零向量");
                    for (int i = 0; i < source.length(); i++) normalized.put(source.getDouble(i) / magnitude);
                    vectors.put(normalized);
                }
                return vectors.toString();
            } catch (Exception error) { return failure(error); } finally { if (connection != null) connection.disconnect(); }
        }

        @JavascriptInterface public String webSearch(String raw) {
            HttpURLConnection connection = null;
            try {
                JSONObject routedRequest=new JSONObject(raw);
                String routedProvider=routedRequest.optString("provider","builtin");
                if(!"builtin".equals(routedProvider)){
                    String routedQuery=routedRequest.optString("query").trim(), routedKey=routedRequest.optString("api_key",""), routedCustomEndpoint=routedRequest.optString("endpoint","");
                    int routedLimit=Math.max(1,Math.min(routedRequest.optInt("max_results",5),8));
                    if(routedQuery.isEmpty())throw new Exception("Search query is required");
                    String routedEndpoint; JSONObject routedPayload=null;
                    if("tavily".equals(routedProvider)){
                        if(routedKey.isEmpty())throw new Exception("Tavily API Key is required");
                        routedEndpoint="https://api.tavily.com/search";
                        routedPayload=new JSONObject().put("api_key",routedKey).put("query",routedQuery).put("max_results",routedLimit).put("search_depth","advanced");
                    }else if("brave".equals(routedProvider)){
                        if(routedKey.isEmpty())throw new Exception("Brave Search API Key is required");
                        routedEndpoint="https://api.search.brave.com/res/v1/web/search?q="+URLEncoder.encode(routedQuery,"UTF-8")+"&count="+routedLimit;
                    }else{
                        if(routedCustomEndpoint.isEmpty())throw new Exception("Custom search endpoint is required");
                        routedEndpoint=routedCustomEndpoint;
                        routedPayload=new JSONObject().put("query",routedQuery).put("max_results",routedLimit);
                    }
                    connection=(HttpURLConnection)new URL(routedEndpoint).openConnection();
                    connection.setConnectTimeout(15000);connection.setReadTimeout(30000);
                    connection.setRequestProperty("Accept","application/json");connection.setRequestProperty("User-Agent","Atherloom/0.5 Android");
                    if("brave".equals(routedProvider))connection.setRequestProperty("X-Subscription-Token",routedKey);
                    else{
                        connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json");
                        if("custom".equals(routedProvider)&&!routedKey.isEmpty())connection.setRequestProperty("Authorization","Bearer "+routedKey);
                        try(OutputStream output=connection.getOutputStream()){output.write(routedPayload.toString().getBytes(StandardCharsets.UTF_8));}
                    }
                    int routedStatus=connection.getResponseCode();String routedResponse=read(routedStatus>=400?connection.getErrorStream():connection.getInputStream());
                    if(routedStatus>=400)throw new Exception("Search HTTP "+routedStatus);
                    JSONObject routedData=new JSONObject(routedResponse);JSONArray sourceRows;
                    if("brave".equals(routedProvider)){JSONObject web=routedData.optJSONObject("web");sourceRows=web==null?null:web.optJSONArray("results");}
                    else sourceRows=routedData.optJSONArray("results");
                    JSONArray normalized=new JSONArray();
                    if(sourceRows!=null)for(int i=0;i<sourceRows.length()&&normalized.length()<routedLimit;i++){
                        JSONObject row=sourceRows.optJSONObject(i);if(row==null)continue;
                        String url=row.optString("url");if(url.isEmpty())continue;
                        normalized.put(new JSONObject().put("title",row.optString("title",routedQuery)).put("url",url).put("snippet",row.optString("content",row.optString("description",row.optString("snippet","")))).put("source",routedProvider));
                    }
                    return new JSONObject().put("query",routedQuery).put("effective_query",routedQuery).put("results",normalized).put("result_count",normalized.length()).put("notice",normalized.length()>0?"":"Search returned no results").toString();
                }
                JSONObject request=new JSONObject(raw);String query=request.optString("query").trim();int limit=Math.max(1,Math.min(request.optInt("max_results",5),8));
                if(query.isEmpty())throw new Exception("搜索关键词不能为空");
                boolean generic=query.matches("^(随便看看|随便搜搜|看看新闻|今日热点|有什么新闻|最近有什么|搜点有趣的)$");
                String effective=generic?"科技 OR 文化 OR 科学 OR 艺术":query;JSONArray results=new JSONArray();
                try {
                    String endpoint="https://api.gdeltproject.org/api/v2/doc/doc?query="+URLEncoder.encode(effective,"UTF-8")+"&mode=artlist&maxrecords="+limit+"&format=json&sort=hybridrel";
                    connection=(HttpURLConnection)new URL(endpoint).openConnection();connection.setConnectTimeout(15000);connection.setReadTimeout(20000);connection.setRequestProperty("User-Agent","Atherloom/0.5 Android");
                    int status=connection.getResponseCode();if(status<400){JSONObject data=new JSONObject(read(connection.getInputStream()));JSONArray articles=data.optJSONArray("articles");if(articles!=null)for(int i=0;i<articles.length()&&results.length()<limit;i++){JSONObject row=articles.optJSONObject(i);if(row!=null&&!row.optString("url").isEmpty())results.put(new JSONObject().put("title",row.optString("title",effective)).put("url",row.optString("url")).put("snippet",(row.optString("domain")+" · "+row.optString("seendate")).replaceAll("^ · | · $","")).put("source","GDELT"));}}
                } catch(Exception ignored) {} finally {if(connection!=null){connection.disconnect();connection=null;}}
                if(results.length()==0)try {
                    String endpoint="https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch="+URLEncoder.encode(effective,"UTF-8")+"&srlimit="+limit+"&format=json&origin=*";
                    connection=(HttpURLConnection)new URL(endpoint).openConnection();connection.setConnectTimeout(15000);connection.setReadTimeout(20000);connection.setRequestProperty("User-Agent","Atherloom/0.5 Android");
                    int status=connection.getResponseCode();if(status<400){JSONObject data=new JSONObject(read(connection.getInputStream())).optJSONObject("query");JSONArray rows=data==null?null:data.optJSONArray("search");if(rows!=null)for(int i=0;i<rows.length()&&results.length()<limit;i++){JSONObject row=rows.optJSONObject(i);if(row!=null){String title=row.optString("title");results.put(new JSONObject().put("title",title).put("url","https://zh.wikipedia.org/wiki/"+URLEncoder.encode(title.replace(" ","_"),"UTF-8")).put("snippet",row.optString("snippet").replaceAll("<[^>]+>","")).put("source","Wikipedia"));}}}
                } catch(Exception ignored) {}
                return new JSONObject().put("query",query).put("effective_query",effective).put("results",results).put("result_count",results.length()).put("notice",results.length()>0?"":"搜索通道暂时没有返回结果，请换一个更具体的关键词。").toString();
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
                if (protocol.equals("anthropic") && request.has("system")) {
                    String cacheMode=provider.optString("cache_mode","auto");
                    String system=request.getString("system"), marker="\n\n<runtime_context>\n"; int markerIndex=system.indexOf(marker);
                    String stable=markerIndex>=0?system.substring(0,markerIndex):system, runtime=markerIndex>=0?system.substring(markerIndex+2):"";
                    JSONArray blocks=new JSONArray(); JSONObject stableBlock=new JSONObject().put("type","text").put("text",stable);
                    if((cacheMode.equals("auto")||cacheMode.equals("anthropic"))&&provider.optBoolean("prompt_cache",true)&&!stable.isEmpty())stableBlock.put("cache_control",new JSONObject().put("type","ephemeral"));
                    blocks.put(stableBlock); if(!runtime.isEmpty())blocks.put(new JSONObject().put("type","text").put("text",runtime)); payload.put("system",blocks);
                }
                if (!protocol.equals("anthropic") && provider.optString("cache_mode").equals("openai") && !provider.optString("prompt_cache_key").isEmpty()) payload.put("prompt_cache_key", provider.optString("prompt_cache_key"));
                if (request.has("tools")) {
                    JSONArray requested = request.getJSONArray("tools"), tools = new JSONArray();
                    for (int i=0;i<requested.length();i++) {
                        JSONObject tool=requested.getJSONObject(i);
                        tools.put(protocol.equals("anthropic") ? new JSONObject().put("name",tool.getString("name")).put("description",tool.optString("description")).put("input_schema",tool.getJSONObject("input_schema")) : new JSONObject().put("type","function").put("function",new JSONObject().put("name",tool.getString("name")).put("description",tool.optString("description")).put("parameters",tool.getJSONObject("input_schema"))));
                    }
                    payload.put("tools",tools);
                }
                boolean reasoningModel = protocol.equals("deepseek") || protocol.equals("glm") || provider.optString("model").toLowerCase(java.util.Locale.ROOT).contains("deepseek");
                if (reasoningModel && request.optBoolean("thinking_enabled", provider.optBoolean("thinking_enabled", true))) payload.put("thinking", new JSONObject().put("type", "enabled"));
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
                if(toolCalls.length()==0)toolCalls=parseDsmlToolCalls(content);
                JSONObject responseMessage = protocol.equals("anthropic") ? null : data.getJSONArray("choices").getJSONObject(0).getJSONObject("message");
                String reasoning = protocol.equals("anthropic") ? "" : nullableString(responseMessage, "reasoning_content"); if (reasoning.isEmpty()) reasoning=nullableString(responseMessage, "reasoning");
                return new JSONObject().put("ok", true).put("content", content).put("reasoning", reasoning).put("model", provider.optString("model")).put("tool_calls",toolCalls).put("raw_assistant",rawAssistant).put("usage",data.optJSONObject("usage")).toString();
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
                    boolean reasoningModel = protocol.equals("deepseek") || protocol.equals("glm") || provider.optString("model").toLowerCase(java.util.Locale.ROOT).contains("deepseek");
                    if (reasoningModel && request.optBoolean("thinking_enabled", provider.optBoolean("thinking_enabled", true))) payload.put("thinking", new JSONObject().put("type", "enabled"));
                    connection = (HttpURLConnection)new URL(endpoint).openConnection(); connection.setRequestMethod("POST"); connection.setConnectTimeout(25000); connection.setReadTimeout(180000); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
                    String apiKey = provider.optString("api_key");
                    if (protocol.equals("anthropic")) { connection.setRequestProperty("x-api-key", apiKey); connection.setRequestProperty("anthropic-version", "2023-06-01"); }
                    else connection.setRequestProperty("Authorization", "Bearer " + apiKey);
                    JSONObject custom = new JSONObject(provider.optString("custom_headers", "{}"));
                    for (Iterator<String> keys = custom.keys(); keys.hasNext();) { String header = keys.next(); connection.setRequestProperty(header, custom.getString(header)); }
                    try (OutputStream output = connection.getOutputStream()) { output.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
                    int status = connection.getResponseCode();
                    if (status >= 400) { String response = read(connection.getErrorStream()); throw new Exception("HTTP " + status + " · " + response.substring(0, Math.min(300, response.length()))); }
                    JSONObject usage = new JSONObject();
                    try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                        String line;
                        while ((line = reader.readLine()) != null) {
                            if (!line.startsWith("data:")) continue;
                            String rawEvent = line.substring(5).trim(); if (rawEvent.isEmpty() || rawEvent.equals("[DONE]")) continue;
                            JSONObject event = new JSONObject(rawEvent), output = new JSONObject(), eventUsage = event.optJSONObject("usage");
                            JSONObject message = event.optJSONObject("message"); if (eventUsage == null && message != null) eventUsage = message.optJSONObject("usage");
                            if (eventUsage != null) for (Iterator<String> keys = eventUsage.keys(); keys.hasNext();) { String key = keys.next(); usage.put(key, eventUsage.get(key)); }
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
                    emitStream(callbackId, new JSONObject().put("done", true).put("model", provider.optString("model")).put("usage", usage.length() > 0 ? usage : JSONObject.NULL));
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

        private static JSONArray parseDsmlToolCalls(String content) throws Exception {
            JSONArray calls = new JSONArray();
            String marker = "[|｜]\\s*DSML\\s*[|｜]";
            Pattern invokePattern = Pattern.compile("<" + marker + "\\s*invoke\\b([^>]*)>([\\s\\S]*?)<" + marker + "\\s*/\\s*invoke\\s*>", Pattern.CASE_INSENSITIVE);
            Pattern parameterPattern = Pattern.compile("<" + marker + "\\s*parameter\\b([^>]*)>([\\s\\S]*?)<" + marker + "\\s*/\\s*parameter\\s*>", Pattern.CASE_INSENSITIVE);
            Pattern namePattern = Pattern.compile("\\bname\\s*=\\s*[\"']([^\"']+)[\"']", Pattern.CASE_INSENSITIVE);
            Matcher invoke = invokePattern.matcher(content == null ? "" : content);
            while (invoke.find()) {
                Matcher toolName = namePattern.matcher(invoke.group(1));
                if (!toolName.find()) continue;
                JSONObject arguments = new JSONObject();
                Matcher parameter = parameterPattern.matcher(invoke.group(2));
                while (parameter.find()) {
                    Matcher parameterName = namePattern.matcher(parameter.group(1));
                    if (!parameterName.find()) continue;
                    String rawValue = parameter.group(2).trim();
                    boolean stringValue = !Pattern.compile("\\bstring\\s*=\\s*[\"']false[\"']", Pattern.CASE_INSENSITIVE).matcher(parameter.group(1)).find();
                    Object value = rawValue;
                    if (!stringValue) {
                        try { value = new JSONTokener(rawValue).nextValue(); }
                        catch (Exception ignored) { value = rawValue; }
                    }
                    arguments.put(parameterName.group(1).trim(), value);
                }
                calls.put(new JSONObject()
                    .put("id", "dsml-" + java.util.UUID.randomUUID())
                    .put("name", toolName.group(1).trim())
                    .put("arguments", arguments)
                    .put("source", "dsml"));
            }
            return calls;
        }

        private static String read(InputStream stream) throws Exception { if(stream==null)return ""; StringBuilder text=new StringBuilder(); try(BufferedReader reader=new BufferedReader(new InputStreamReader(stream,StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null)text.append(line).append('\n');} return text.toString().trim(); }
        private static String failure(Exception error) { try { return new JSONObject().put("ok",false).put("error",error.getMessage()).toString(); } catch(Exception ignored){ return "{\"ok\":false,\"error\":\"unknown\"}"; } }
    }

    @Override public void onBackPressed() {
        webView.evaluateJavascript("window.AtherloomHandleBack ? window.AtherloomHandleBack() : false", handled -> {
            if (!"true".equals(handled)) { if (webView.canGoBack()) webView.goBack(); else MainActivity.super.onBackPressed(); }
        });
    }
    @Override protected void onDestroy() {
        if (liveWebView.get() == webView) liveWebView.clear();
        super.onDestroy();
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
