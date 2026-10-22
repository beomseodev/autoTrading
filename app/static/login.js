"use strict";
fetch("/auth/session").then(response => response.json()).then(data => {
  if (data.user) { location.replace("/"); return; }
  document.querySelector("#login-status").textContent = data.configured ? "본인 전용 서비스입니다. 다른 계정은 접근할 수 없습니다." : "Google 로그인 설정이 필요합니다. 서버의 .env 설정과 README를 확인해주세요.";
  if (!data.configured) {
    document.querySelector("#google-login").removeAttribute("href");
    document.querySelector("#google-login").setAttribute("aria-disabled", "true");
  }
}).catch(() => { document.querySelector("#login-status").textContent = "서버에 연결하지 못했습니다. 새로고침해주세요."; });
