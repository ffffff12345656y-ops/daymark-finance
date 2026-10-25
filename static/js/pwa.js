if ("serviceWorker" in navigator) {
    let updateBanner;
    let hasController = Boolean(navigator.serviceWorker.controller);
    let reloading = false;

    const showUpdateBanner = () => {
        if (updateBanner || !document.body) return;

        updateBanner = document.createElement("aside");
        updateBanner.className = "pwa-update-banner";
        updateBanner.setAttribute("role", "status");
        updateBanner.setAttribute("aria-live", "polite");
        updateBanner.innerHTML = `
            <span>มีฟีเจอร์ใหม่! อัปเดตแอปเป็นเวอร์ชันล่าสุด</span>
            <button class="pwa-update-action" type="button">อัปเดตทันที</button>
            <button class="pwa-update-dismiss" type="button" aria-label="ปิดการแจ้งเตือน">ปิด</button>
        `;

        const style = document.createElement("style");
        style.textContent = `
            .pwa-update-banner {
                position: fixed;
                z-index: 10000;
                right: 16px;
                bottom: max(16px, env(safe-area-inset-bottom));
                left: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 12px;
                padding: 14px 16px;
                border: 1px solid #3caa83;
                border-radius: 8px;
                background: #17211f;
                color: #eff2e9;
                box-shadow: 0 8px 28px #0005;
                font: 500 14px/1.5 'Kanit', sans-serif;
            }
            .pwa-update-banner button {
                flex: 0 0 auto;
                min-height: 38px;
                padding: 7px 12px;
                border: 0;
                border-radius: 6px;
                cursor: pointer;
                font: inherit;
            }
            .pwa-update-action { background: #75d6b1; color: #10211c; }
            .pwa-update-dismiss { background: transparent; color: #eff2e9; }
            @media (max-width: 540px) {
                .pwa-update-banner { flex-wrap: wrap; justify-content: flex-start; }
                .pwa-update-banner span { flex-basis: 100%; }
                .pwa-update-dismiss { margin-left: auto; }
            }
        `;
        document.head.append(style);
        document.body.append(updateBanner);

        updateBanner.querySelector(".pwa-update-action").addEventListener("click", () => {
            navigator.serviceWorker.getRegistration("/").then((registration) => {
                registration?.waiting?.postMessage({ type: "SKIP_WAITING" });
            });
        });
        updateBanner.querySelector(".pwa-update-dismiss").addEventListener("click", () => {
            updateBanner.remove();
            updateBanner = null;
        });
    };

    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js", {
            scope: "/",
            updateViaCache: "none",
        }).then((registration) => {
            if (registration.waiting && navigator.serviceWorker.controller) {
                showUpdateBanner();
            }

            registration.addEventListener("updatefound", () => {
                const installingWorker = registration.installing;
                installingWorker?.addEventListener("statechange", () => {
                    if (installingWorker.state === "installed" && navigator.serviceWorker.controller) {
                        showUpdateBanner();
                    }
                });
            });

            registration.update().catch(() => { });
        }).catch((error) => {
            console.error("Service worker registration failed:", error);
        });
    });

    navigator.serviceWorker.addEventListener("message", (event) => {
        if (event.data?.type === "UPDATE_READY" && navigator.serviceWorker.controller) {
            showUpdateBanner();
        }
    });

    navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (hasController && !reloading) {
            reloading = true;
            window.location.reload();
            return;
        }
        hasController = true;
    });
}