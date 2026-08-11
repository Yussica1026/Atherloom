package app.atherloom.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import org.json.JSONObject;

public class AutonomyService extends Service {
    public static final String ACTION_START = "app.atherloom.mobile.AUTONOMY_START";
    public static final String ACTION_STOP = "app.atherloom.mobile.AUTONOMY_STOP";
    private static final String CHANNEL_ID = "atherloom_autonomy";
    private static final int NOTIFICATION_ID = 7021;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable wake = new Runnable() {
        @Override public void run() {
            JSONObject config = config();
            if (!config.optBoolean("enabled", false)) { stopSelf(); return; }
            boolean delivered = MainActivity.runAutonomyWake();
            showNotification(delivered ? "AI 已被唤醒，正在自主活动" : "等待你重新打开一次 Atherloom");
            handler.postDelayed(this, intervalMillis(config));
        }
    };

    @Override public void onCreate() { super.onCreate(); createChannel(); }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            handler.removeCallbacks(wake); stopForeground(true); stopSelf(); return START_NOT_STICKY;
        }
        showNotification("自主生活计划运行中");
        handler.removeCallbacks(wake);
        handler.postDelayed(wake, intervalMillis(config()));
        return START_STICKY;
    }

    private JSONObject config() {
        SharedPreferences prefs = getSharedPreferences("atherloom_runtime", MODE_PRIVATE);
        try { return new JSONObject(prefs.getString("autonomy_config", "{}")); }
        catch (Exception ignored) { return new JSONObject(); }
    }

    private long intervalMillis(JSONObject config) {
        return Math.max(15, Math.min(1440, config.optInt("interval_minutes", 60))) * 60_000L;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "AI 自主生活", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("定时唤醒人格进行旅行、游戏与日记活动");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private void showNotification(String text) {
        Intent openIntent = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent open = PendingIntent.getActivity(this, 1, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stopIntent = new Intent(this, AutonomyService.class).setAction(ACTION_STOP);
        PendingIntent stop = PendingIntent.getService(this, 2, stopIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL_ID) : new Notification.Builder(this);
        Notification notification = builder.setSmallIcon(R.drawable.ic_launcher).setContentTitle("Atherloom · AI 自主生活")
            .setContentText(text).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .addAction(new Notification.Action.Builder(null, "停止计划", stop).build()).build();
        startForeground(NOTIFICATION_ID, notification);
    }

    @Override public void onDestroy() { handler.removeCallbacks(wake); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}
