const state = {
    dashboard: null,
    charts: {},
    largeChart: null,
    countdown: 30,
    currentFormImageUrl: "",
};

const ui = {
    addServiceButton: document.getElementById("add-service-button"),
    overviewGrid: document.getElementById("overview-grid"),
    servicesGrid: document.getElementById("services-grid"),
    servicesCaption: document.getElementById("services-caption"),
    nextUpdateTimer: document.getElementById("next-update-timer"),
    lastGlobalUpdate: document.getElementById("last-global-update"),
    detailsModal: document.getElementById("detailsModal"),
    detailsName: document.getElementById("detailsName"),
    detailsHost: document.getElementById("detailsHost"),
    detailsMetrics: document.getElementById("detailsMetrics"),
    detailsStatusLabel: document.getElementById("detailsStatusLabel"),
    detailsThresholdLabel: document.getElementById("detailsThresholdLabel"),
    largeChartCanvas: document.getElementById("largeChart"),
    closeDetailsButton: document.getElementById("close-details-button"),
    addModal: document.getElementById("addModal"),
    modalTitle: document.getElementById("modalTitle"),
    editServiceId: document.getElementById("editServiceId"),
    serviceName: document.getElementById("serviceName"),
    serviceHost: document.getElementById("serviceHost"),
    serviceThreshold: document.getElementById("serviceThreshold"),
    serviceImageFile: document.getElementById("serviceImageFile"),
    serviceImageHint: document.getElementById("serviceImageHint"),
    closeFormButton: document.getElementById("close-form-button"),
    cancelFormButton: document.getElementById("cancel-form-button"),
    saveServiceButton: document.getElementById("save-service-button"),
};

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function formatLatency(value) {
    return typeof value === "number" ? `${value.toFixed(1)} ms` : "Sem resposta";
}

function formatCompactLatency(value) {
    return typeof value === "number" ? `${Math.round(value)} ms` : "—";
}

function formatPercent(value) {
    return typeof value === "number" ? `${value.toFixed(1)}%` : "—";
}

function formatDateTime(value) {
    if (!value) return "Sem medições ainda";
    return new Date(value).toLocaleString("pt-BR");
}

function getStatusMeta(status) {
    if (status === "offline") {
        return { label: "Offline", chipClass: "status-offline", accentClass: "accent-offline" };
    }
    if (status === "degraded") {
        return { label: "Degradado", chipClass: "status-degraded", accentClass: "accent-degraded" };
    }
    return { label: "Online", chipClass: "status-online", accentClass: "accent-online" };
}

async function requestJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: "Erro de requisição." }));
        throw new Error(payload.error || `Falha em ${url}`);
    }
    return response.json();
}

function updateTimerLabel() {
    ui.nextUpdateTimer.textContent = `Próxima atualização em ${state.countdown}s`;
}

function renderHeroMeta() {
    const { meta, summary } = state.dashboard;
    ui.lastGlobalUpdate.textContent = `Última atualização: ${formatDateTime(meta.lastUpdate)}`;
    ui.servicesCaption.textContent =
        `${summary.total} serviços monitorados • ${summary.offline} offline • ${summary.degraded} degradados`;
    updateTimerLabel();
}

function renderOverview() {
    const { summary } = state.dashboard;
    const cards = [
        {
            title: "Serviços monitorados",
            value: summary.total,
            tone: "tone-neutral",
        },
        {
            title: "Saudáveis",
            value: `${summary.online}/${summary.total}`,
            tone: "tone-good",
        },
        {
            title: "Latência média",
            value: summary.avgLatencyMs != null ? `${summary.avgLatencyMs.toFixed(1)} ms` : "—",
            tone: "tone-latency",
        },
        {
            title: "Estabilidade média",
            value: summary.avgStabilityPct != null ? `${summary.avgStabilityPct.toFixed(1)}%` : "—",
            tone: "tone-stability",
        },
    ];

    ui.overviewGrid.innerHTML = cards.map((card) => `
        <article class="overview-card panel ${card.tone}">
            <span>${card.title}</span>
            <strong>${card.value}</strong>
        </article>
    `).join("");
}

function destroyMiniCharts() {
    Object.values(state.charts).forEach((chart) => chart.destroy());
    state.charts = {};
}

function renderServiceCard(service) {
    const statusMeta = getStatusMeta(service.status);
    const initials = service.name
        .split(" ")
        .map((part) => part[0] || "")
        .join("")
        .slice(0, 2)
        .toUpperCase();
    const hasImage = Boolean(service.imageUrl);

    return `
        <article class="service-card panel ${statusMeta.accentClass}" data-action="open-details" data-service-id="${service.id}">
            <div class="service-card-header">
                <div class="service-title-block">
                    <h3>${escapeHtml(service.name)}</h3>
                    <span class="status-chip ${statusMeta.chipClass}">${statusMeta.label}</span>
                </div>

                <div class="service-actions">
                    <button class="icon-button subtle" type="button" data-action="edit-service" data-service-id="${service.id}">✎</button>
                    <button class="icon-button subtle danger" type="button" data-action="remove-service" data-service-id="${service.id}">×</button>
                </div>
            </div>

            <div class="service-avatar-panel">
                <div class="service-avatar ${hasImage ? "has-image" : ""}" data-service-avatar="${service.id}">
                    ${hasImage ? `<img src="${escapeHtml(service.imageUrl)}" alt="${escapeHtml(service.name)}">` : ""}
                    <span class="service-avatar-fallback">${escapeHtml(initials)}</span>
                </div>
            </div>

            <div class="service-chart">
                <canvas id="chart-${service.id}"></canvas>
            </div>

            <div class="metrics-grid metrics-grid-compact">
                <div class="metric-box">
                    <span>Latência</span>
                    <strong>${formatCompactLatency(service.avgLatencyMs)}</strong>
                </div>
                <div class="metric-box">
                    <span>Jitter</span>
                    <strong>${formatCompactLatency(service.jitterMs)}</strong>
                </div>
                <div class="metric-box">
                    <span>Limiar</span>
                    <strong>${formatCompactLatency(service.threshold)}</strong>
                </div>
                <div class="metric-box">
                    <span>Perda</span>
                    <strong>${formatPercent(service.packetLossPct)}</strong>
                </div>
            </div>
        </article>
    `;
}

function createMiniChart(service, points) {
    const canvas = document.getElementById(`chart-${service.id}`);
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const color = service.status === "offline"
        ? "#f97316"
        : service.status === "degraded"
            ? "#f59e0b"
            : "#22c55e";

    state.charts[service.id] = new Chart(ctx, {
        type: "line",
        data: {
            labels: points.map((point) => point.timestamp),
            datasets: [{
                data: points.map((point) => point.latencyMs),
                borderColor: color,
                backgroundColor: `${color}22`,
                fill: true,
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.35,
                spanGaps: true,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false },
            },
            scales: {
                x: { display: false },
                y: { display: false, beginAtZero: true },
            },
        },
    });
}

function renderServices() {
    const { services, history } = state.dashboard;
    destroyMiniCharts();

    if (!services.length) {
        ui.servicesGrid.innerHTML = `
            <article class="empty-state panel">
                <h3>Nenhum serviço cadastrado</h3>
                <p>Adicione um host para começar a acompanhar latência, perda e estabilidade.</p>
            </article>
        `;
        return;
    }

    ui.servicesGrid.innerHTML = services.map(renderServiceCard).join("");
    bindAvatarFallbacks();
    services.forEach((service) => createMiniChart(service, history[service.id] || []));
}

function bindAvatarFallbacks() {
    ui.servicesGrid.querySelectorAll(".service-avatar.has-image img").forEach((image) => {
        image.addEventListener("error", () => {
            image.parentElement.classList.remove("has-image");
            image.remove();
        }, { once: true });
    });
}

function renderLargeChart(service, points) {
    const ctx = ui.largeChartCanvas.getContext("2d");

    if (state.largeChart) {
        state.largeChart.destroy();
    }

    state.largeChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: points.map((point) =>
                new Date(point.timestamp).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
            ),
            datasets: [
                {
                    label: "Latência média",
                    data: points.map((point) => point.latencyMs),
                    borderColor: "#2563eb",
                    backgroundColor: "rgba(37, 99, 235, 0.12)",
                    fill: true,
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 2,
                    spanGaps: true,
                },
                {
                    label: "Limiar",
                    data: points.map(() => service.threshold),
                    borderColor: "#f97316",
                    borderDash: [6, 6],
                    borderWidth: 1.5,
                    pointRadius: 0,
                    spanGaps: true,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: "#dbe7ff",
                        usePointStyle: true,
                        pointStyle: "circle",
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: "#94a3b8" },
                    grid: { color: "rgba(148, 163, 184, 0.12)" },
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: "#94a3b8" },
                    grid: { color: "rgba(148, 163, 184, 0.12)" },
                },
            },
        },
    });
}

function openDetails(serviceId) {
    const service = state.dashboard.services.find((item) => item.id === serviceId);
    if (!service) return;

    const history = state.dashboard.history[serviceId] || [];
    const statusMeta = getStatusMeta(service.status);

    ui.detailsName.textContent = service.name;
    ui.detailsHost.textContent = service.host;
    ui.detailsStatusLabel.textContent = `${statusMeta.label} • última leitura ${formatDateTime(service.lastUpdate)}`;
    ui.detailsThresholdLabel.textContent = `Limiar: ${formatCompactLatency(service.threshold)}`;
    ui.detailsMetrics.innerHTML = `
        <article class="detail-metric panel">
            <span>Latência média</span>
            <strong>${formatLatency(service.avgLatencyMs)}</strong>
        </article>
        <article class="detail-metric panel">
            <span>Jitter</span>
            <strong>${formatLatency(service.jitterMs)}</strong>
        </article>
        <article class="detail-metric panel">
            <span>Perda de pacotes</span>
            <strong>${formatPercent(service.packetLossPct)}</strong>
        </article>
        <article class="detail-metric panel">
            <span>Estabilidade</span>
            <strong>${formatPercent(service.stabilityPct)}</strong>
        </article>
    `;

    renderLargeChart(service, history);
    ui.detailsModal.style.display = "flex";
}

function closeDetails() {
    ui.detailsModal.style.display = "none";
}

function updateImageHint() {
    const hasSelectedFile = Boolean(ui.serviceImageFile.files && ui.serviceImageFile.files.length);
    const hasStoredImage = Boolean(state.currentFormImageUrl);

    if (hasSelectedFile) {
        ui.serviceImageHint.textContent = "Nova imagem selecionada para este serviço.";
        return;
    }

    if (hasStoredImage) {
        ui.serviceImageHint.textContent = "Nenhum novo arquivo selecionado. A imagem atual será mantida.";
        return;
    }

    ui.serviceImageHint.textContent = "Selecione uma imagem local para substituir o avatar.";
}

function readImageFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
        reader.onerror = () => reject(new Error("Falha ao ler a imagem selecionada."));
        reader.readAsDataURL(file);
    });
}

function openCreateModal() {
    ui.modalTitle.textContent = "Adicionar serviço";
    ui.editServiceId.value = "";
    ui.serviceName.value = "";
    ui.serviceHost.value = "";
    ui.serviceThreshold.value = "100";
    ui.serviceImageFile.value = "";
    state.currentFormImageUrl = "";
    updateImageHint();
    ui.addModal.style.display = "flex";
}

function openEditModal(serviceId) {
    const service = state.dashboard.services.find((item) => item.id === serviceId);
    if (!service) return;

    ui.modalTitle.textContent = "Editar serviço";
    ui.editServiceId.value = serviceId;
    ui.serviceName.value = service.name;
    ui.serviceHost.value = service.host;
    ui.serviceThreshold.value = service.threshold ?? 100;
    ui.serviceImageFile.value = "";
    state.currentFormImageUrl = service.imageUrl ?? "";
    updateImageHint();
    ui.addModal.style.display = "flex";
}

function closeFormModal() {
    ui.addModal.style.display = "none";
}

async function saveService() {
    let imageUrl = state.currentFormImageUrl;
    const selectedFile = ui.serviceImageFile.files && ui.serviceImageFile.files[0];

    if (selectedFile) {
        imageUrl = await readImageFileAsDataUrl(selectedFile);
    }

    const payload = {
        name: ui.serviceName.value.trim(),
        host: ui.serviceHost.value.trim(),
        threshold: Number(ui.serviceThreshold.value),
        imageUrl,
    };

    if (!payload.name || !payload.host) {
        alert("Preencha nome e host do serviço.");
        return;
    }

    const serviceId = ui.editServiceId.value;
    await requestJson(serviceId ? `/api/services/${serviceId}` : "/api/services", {
        method: serviceId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    closeFormModal();
    await fetchDashboard();
}

async function removeService(serviceId) {
    if (!confirm("Deseja remover este serviço do monitoramento?")) {
        return;
    }

    await requestJson(`/api/services/${serviceId}`, { method: "DELETE" });
    await fetchDashboard();
}

function handleServiceGridClick(event) {
    const editButton = event.target.closest('[data-action="edit-service"]');
    if (editButton) {
        event.stopPropagation();
        openEditModal(editButton.dataset.serviceId);
        return;
    }

    const removeButton = event.target.closest('[data-action="remove-service"]');
    if (removeButton) {
        event.stopPropagation();
        removeService(removeButton.dataset.serviceId);
        return;
    }

    const card = event.target.closest('[data-action="open-details"]');
    if (card) {
        openDetails(card.dataset.serviceId);
    }
}

function bindEvents() {
    ui.addServiceButton.addEventListener("click", openCreateModal);
    ui.closeDetailsButton.addEventListener("click", closeDetails);
    ui.closeFormButton.addEventListener("click", closeFormModal);
    ui.cancelFormButton.addEventListener("click", closeFormModal);
    ui.saveServiceButton.addEventListener("click", saveService);
    ui.serviceImageFile.addEventListener("change", updateImageHint);
    ui.servicesGrid.addEventListener("click", handleServiceGridClick);

    ui.detailsModal.addEventListener("click", (event) => {
        if (event.target === ui.detailsModal) {
            closeDetails();
        }
    });

    ui.addModal.addEventListener("click", (event) => {
        if (event.target === ui.addModal) {
            closeFormModal();
        }
    });
}

async function fetchDashboard() {
    state.dashboard = await requestJson("/api/dashboard");
    state.countdown = state.dashboard.meta?.nextUpdateSeconds || 30;
    renderHeroMeta();
    renderOverview();
    renderServices();
}

function startRefreshLoop() {
    setInterval(async () => {
        state.countdown -= 1;
        if (state.countdown <= 0) {
            try {
                await fetchDashboard();
            } catch (error) {
                console.error(error);
                state.countdown = 5;
            }
        }
        updateTimerLabel();
    }, 1000);
}

async function bootstrap() {
    bindEvents();
    startRefreshLoop();

    try {
        await fetchDashboard();
    } catch (error) {
        console.error(error);
        ui.servicesGrid.innerHTML = `
            <article class="empty-state panel">
                <h3>Falha ao carregar o painel</h3>
                <p>Verifique se o servidor Python está ativo e tente novamente.</p>
            </article>
        `;
    }
}

bootstrap();
