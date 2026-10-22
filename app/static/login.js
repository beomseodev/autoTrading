"use strict";
fetch("/auth/session").then(response => response.json()).then(data => {
  if (data.user) { location.replace("/"); return; }
  document.querySelector("#login-status").textContent = data.configured ? "저장하려면 Google 계정으로 로그인해주세요. 로그인 없이도 저장을 제외한 기능을 이용할 수 있습니다." : "Google 로그인 설정이 필요합니다. 서버의 .env 설정과 README를 확인해주세요.";
  if (!data.configured) {
    document.querySelector("#google-login").removeAttribute("href");
    document.querySelector("#google-login").setAttribute("aria-disabled", "true");
  }
}).catch(() => { document.querySelector("#login-status").textContent = "서버에 연결하지 못했습니다. 새로고침해주세요."; });
