"use strict";
self.addEventListener('push',event=>{
  let tag='stock-alerts';
  try {const value=event.data.json();if(/^stock-alerts-[a-f0-9-]{36}$/.test(value.tag))tag=value.tag;} catch {}
  event.waitUntil(self.registration.showNotification('관심종목 알림',{
    body:'새 알림이 있습니다. 로그인 후 이력을 확인해주세요.',tag,renotify:false,data:{url:'/alerts'}
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(self.clients.openWindow('/alerts'));
});
