function selectFinanceTab(tablist, selectedTab, moveFocus = false) {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    for (const tab of tabs) {
        const selected = tab === selectedTab;
        tab.setAttribute("aria-selected", String(selected));
        document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
    }
    if (selectedTab.id === "finance-history-tab" && window.Chart) {
        const growthCanvas = document.getElementById("savings-growth-chart");
        if (growthCanvas) window.Chart.getChart(growthCanvas)?.resize();
    }
    if (moveFocus) selectedTab.focus();
}

document.querySelectorAll('[role="tablist"]').forEach((tablist) => {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => selectFinanceTab(tablist, tab));
        tab.addEventListener("keydown", (event) => {
            let nextIndex;
            if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
            if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
            if (event.key === "Home") nextIndex = 0;
            if (event.key === "End") nextIndex = tabs.length - 1;
            if (nextIndex !== undefined) {
                event.preventDefault();
                selectFinanceTab(tablist, tabs[nextIndex], true);
            }
        });
    });
});

function revealExpectedIncomeSection() {
    if (window.location.hash !== "#expected-income-title") return;
    const pageTabs = document.querySelector('.finance-page-tabs[role="tablist"]');
    const dashboardTab = pageTabs?.querySelector('[aria-controls="finance-dashboard-panel"]');
    if (pageTabs && dashboardTab) selectFinanceTab(pageTabs, dashboardTab);
}

window.addEventListener("hashchange", revealExpectedIncomeSection);
revealExpectedIncomeSection();

const savingsGoalDialog = document.getElementById("savings-goal-dialog");
const openSavingsGoalDialog = document.getElementById("open-savings-goal-dialog");

if (savingsGoalDialog && openSavingsGoalDialog) {
    openSavingsGoalDialog.addEventListener("click", () => {
        savingsGoalDialog.showModal();
        savingsGoalDialog.querySelector('input[name="title"]')?.focus();
    });

    savingsGoalDialog.querySelectorAll("[data-close-savings-goal-dialog]").forEach((button) => {
        button.addEventListener("click", () => savingsGoalDialog.close());
    });

    savingsGoalDialog.addEventListener("click", (event) => {
        if (event.target === savingsGoalDialog) savingsGoalDialog.close();
    });
}

const projectionInput = document.getElementById("projection-months");

if (projectionInput) {
    const currentSavings = Number(projectionInput.dataset.currentSavings);
    const averageMonthlySavings = Number(projectionInput.dataset.averageMonthly);
    const projectionTotals = document.querySelectorAll("[data-projection-total]");
    const projectionMonthLabels = document.querySelectorAll("[data-projection-month-label]");
    const currencyFormatter = new Intl.NumberFormat("th-TH", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });

    projectionInput.addEventListener("input", () => {
        if (projectionInput.value === "") return;
        const months = Math.max(1, Math.min(Math.trunc(Number(projectionInput.value)) || 1, 120));
        const projectedTotal = currentSavings + averageMonthlySavings * months;

        projectionMonthLabels.forEach((label) => {
            label.textContent = String(months);
        });
        projectionTotals.forEach((total) => {
            total.textContent = `฿${currencyFormatter.format(projectedTotal)}`;
        });

        const pageUrl = new URL(window.location.href);
        pageUrl.searchParams.set("projection_months", String(months));
        window.history.replaceState(null, "", pageUrl);
    });
}

const growthCanvas = document.getElementById("savings-growth-chart");
const growthDataElement = document.getElementById("savings-growth-data");

if (growthCanvas && growthDataElement && window.Chart) {
    const growthData = JSON.parse(growthDataElement.textContent);
    new Chart(growthCanvas, {
        type: "line",
        data: {
            labels: growthData.labels,
            datasets: [{
                label: "เงินออมสะสม",
                data: growthData.values,
                borderColor: "#278f79",
                backgroundColor: "rgba(39, 143, 121, 0.12)",
                pointBackgroundColor: "#dc784c",
                pointBorderColor: "#ffffff",
                pointRadius: 3,
                pointHoverRadius: 5,
                borderWidth: 2,
                fill: true,
                tension: 0.28,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: "index" },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (context) => ` ฿${Number(context.raw).toLocaleString("th-TH", { maximumFractionDigits: 2 })}`,
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: "#748179", font: { family: "IBM Plex Sans Thai", size: 10 } },
                },
                y: {
                    grid: { color: "#e8eee9" },
                    ticks: {
                        color: "#748179",
                        font: { family: "DM Sans", size: 9 },
                        callback: (value) => `฿${Number(value).toLocaleString("th-TH", { notation: "compact" })}`,
                    },
                },
            },
        },
    });
}