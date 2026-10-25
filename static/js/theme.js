(() => {
    const storageKey = "daymark-theme";
    const root = document.documentElement;

    const readTheme = () => {
        try {
            return localStorage.getItem(storageKey) === "dark" ? "dark" : "light";
        } catch {
            return "light";
        }
    };

    const applyTheme = (theme) => {
        root.dataset.theme = theme;
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            const isDark = theme === "dark";
            button.textContent = isDark ? "☀" : "☾";
            button.setAttribute("aria-label", isDark ? "เปลี่ยนเป็นโหมดสว่าง" : "เปลี่ยนเป็นโหมดมืด");
            button.setAttribute("aria-pressed", String(isDark));
            button.title = isDark ? "โหมดสว่าง" : "โหมดมืด";
        });
    };

    applyTheme(readTheme());
    document.addEventListener("DOMContentLoaded", () => applyTheme(root.dataset.theme));

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-theme-toggle]");
        if (!button) return;

        const theme = root.dataset.theme === "dark" ? "light" : "dark";
        applyTheme(theme);
        try {
            localStorage.setItem(storageKey, theme);
        } catch { }
    });
})();