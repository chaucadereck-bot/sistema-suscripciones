document.addEventListener("DOMContentLoaded", () => {

    if (typeof Chart === "undefined") {
        console.warn("Chart.js no está cargado");
        return;
    }

    // =========================
    // CONFIG GLOBAL
    // =========================

    Chart.defaults.font.family = "Inter, system-ui, -apple-system, sans-serif";
    Chart.defaults.color = "#6b7280";
    Chart.defaults.plugins.tooltip.backgroundColor = "#111827";
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.plugins.tooltip.displayColors = false;

    const palette = [
        "#2563eb",
        "#22c55e",
        "#f59e0b",
        "#ef4444",
        "#a855f7",
        "#14b8a6"
    ];

    const gridColor = "#e5e7eb";


    // =========================
    // FUNCION GENERICA
    // =========================

    function createChart(canvas, config) {

        if (!canvas) return null;

        const ctx = canvas.getContext("2d");

        return new Chart(ctx, config);

    }


    // =========================
    // CONTABILIDAD
    // =========================

    const finanzasCanvas = document.getElementById("graficoFinanzas");

    if (finanzasCanvas) {

        const ingresos = Number(finanzasCanvas.dataset.ingresos || 0);
        const pagos = Number(finanzasCanvas.dataset.pagos || 0);
        const utilidad = Number(finanzasCanvas.dataset.utilidad || 0);

        if (ingresos || pagos || utilidad) {

            createChart(finanzasCanvas, {

                type: "bar",

                data: {
                    labels: ["Ingresos", "Pagos", "Utilidad"],
                    datasets: [{
                        data: [ingresos, pagos, utilidad],
                        backgroundColor: [
                            palette[1],
                            palette[3],
                            palette[0]
                        ],
                        borderRadius: 8,
                        borderSkipped: false,
                        maxBarThickness: 60
                    }]
                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    animation: { duration: 800 },

                    plugins: {
                        legend: { display: false }
                    },

                    scales: {

                        y: {
                            beginAtZero: true,
                            grid: { color: gridColor }
                        },

                        x: {
                            grid: { display: false }
                        }

                    }

                }

            });

        }

    }



    // =========================
    // DISTRIBUCION FINANCIERA
    // =========================

    const distribucionCanvas = document.getElementById("graficoDistribucion");

    if (distribucionCanvas) {

        const ingresos = Number(distribucionCanvas.dataset.ingresos || 0);
        const pagos = Number(distribucionCanvas.dataset.pagos || 0);
        const utilidad = Number(distribucionCanvas.dataset.utilidad || 0);

        if (pagos || utilidad) {

            createChart(distribucionCanvas, {

                type: "doughnut",

                data: {

                    labels: ["Pagos", "Utilidad"],

                    datasets: [{
                        data: [pagos, utilidad],
                        backgroundColor: [
                            palette[3],
                            palette[0]
                        ],
                        borderWidth: 0,
                        hoverOffset: 8
                    }]

                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    cutout: "70%",

                    plugins: {

                        legend: {
                            position: "bottom",
                            labels: {
                                boxWidth: 12,
                                padding: 16
                            }
                        }

                    },

                    animation: { duration: 800 }

                }

            });

        }

    }



    // =========================
    // SERVICIOS
    // =========================

    const serviciosCanvas = document.getElementById("graficoServicios");

    if (serviciosCanvas) {

        let labels = [];
        let valores = [];

        try {

            labels = JSON.parse(serviciosCanvas.dataset.labels || "[]");
            valores = JSON.parse(serviciosCanvas.dataset.valores || "[]");

        } catch (err) {

            console.error("Error leyendo datos de servicios");

        }

        if (labels.length) {

            createChart(serviciosCanvas, {

                type: "doughnut",

                data: {

                    labels,

                    datasets: [{
                        data: valores,
                        backgroundColor: labels.map((_, i) => palette[i % palette.length]),
                        borderWidth: 0,
                        hoverOffset: 8
                    }]

                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    cutout: "70%",

                    plugins: {

                        legend: {
                            position: "bottom",
                            labels: {
                                boxWidth: 12,
                                padding: 16
                            }
                        }

                    },

                    animation: { duration: 800 }

                }

            });

        }

    }



    // =========================
    // TOP SERVICIOS
    // =========================

    const serviciosTopCanvas = document.getElementById("graficoServiciosTop");

    if (serviciosTopCanvas) {

        let labels = [];
        let valores = [];

        try {

            labels = JSON.parse(serviciosTopCanvas.dataset.labels || "[]");
            valores = JSON.parse(serviciosTopCanvas.dataset.valores || "[]");

        } catch (err) {

            console.error("Error leyendo top servicios");

        }

        if (labels.length) {

            createChart(serviciosTopCanvas, {

                type: "bar",

                data: {

                    labels,

                    datasets: [{
                        data: valores,
                        backgroundColor: palette[0],
                        borderRadius: 8,
                        borderSkipped: false,
                        maxBarThickness: 50
                    }]

                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    animation: { duration: 800 },

                    plugins: {
                        legend: { display: false }
                    },

                    scales: {

                        y: {
                            beginAtZero: true,
                            grid: { color: gridColor }
                        },

                        x: {
                            grid: { display: false }
                        }

                    }

                }

            });

        }

    }

});