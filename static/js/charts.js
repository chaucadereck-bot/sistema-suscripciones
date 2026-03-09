document.addEventListener("DOMContentLoaded", function () {

    // =========================
    // CONFIGURACIÓN GLOBAL
    // =========================

    Chart.defaults.font.family = "Inter";
    Chart.defaults.color = "#6b7280";

    const palette = {
        blue: "#2563eb",
        green: "#22c55e",
        red: "#ef4444",
        yellow: "#f59e0b",
        purple: "#a855f7",
        teal: "#14b8a6",
        grid: "#e5e7eb"
    };


    // =========================
    // CONTABILIDAD
    // =========================

    const finanzasCanvas = document.getElementById("graficoFinanzas");
    const distribucionCanvas = document.getElementById("graficoDistribucion");

    if (finanzasCanvas) {

        const ingresos = Number(finanzasCanvas.dataset.ingresos || 0);
        const pagos = Number(finanzasCanvas.dataset.pagos || 0);
        const utilidad = Number(finanzasCanvas.dataset.utilidad || 0);

        if (ingresos === 0 && pagos === 0 && utilidad === 0) {
            console.warn("No hay datos financieros para graficar");
        } else {

            const ctx = finanzasCanvas.getContext("2d");

            new Chart(ctx, {

                type: "bar",

                data: {
                    labels: ["Ingresos", "Pagos", "Utilidad"],
                    datasets: [{
                        label: "USD",
                        data: [ingresos, pagos, utilidad],
                        backgroundColor: [
                            palette.green,
                            palette.red,
                            palette.blue
                        ],
                        borderRadius: 8,
                        borderSkipped: false
                    }]
                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    layout: {
                        padding: 10
                    },

                    plugins: {

                        legend: {
                            display: false
                        },

                        tooltip: {
                            backgroundColor: "#111827",
                            padding: 12,
                            cornerRadius: 6,
                            displayColors: false
                        }

                    },

                    animation: {
                        duration: 900,
                        easing: "easeOutQuart"
                    },

                    scales: {

                        y: {
                            grid: {
                                color: palette.grid
                            },
                            beginAtZero: true
                        },

                        x: {
                            grid: {
                                display: false
                            }
                        }

                    }

                }

            });


            // =========================
            // DISTRIBUCIÓN
            // =========================

            if (distribucionCanvas) {

                const ctx2 = distribucionCanvas.getContext("2d");

                new Chart(ctx2, {

                    type: "doughnut",

                    data: {
                        labels: ["Pagos", "Utilidad"],
                        datasets: [{
                            data: [pagos, utilidad],
                            backgroundColor: [
                                palette.red,
                                palette.blue
                            ],
                            borderWidth: 0
                        }]
                    },

                    options: {

                        responsive: true,
                        maintainAspectRatio: false,

                        cutout: "65%",

                        plugins: {

                            legend: {
                                position: "bottom",
                                labels: {
                                    padding: 20
                                }
                            }

                        },

                        animation: {
                            duration: 900
                        }

                    }

                });

            }

        }

    }



    // =========================
    // SERVICIOS
    // =========================

    const serviciosCanvas = document.getElementById("graficoServicios");
    const serviciosTopCanvas = document.getElementById("graficoServiciosTop");

    if (serviciosCanvas) {

        let labels = [];
        let valores = [];

        try {

            labels = JSON.parse(serviciosCanvas.dataset.labels || "[]");
            valores = JSON.parse(serviciosCanvas.dataset.valores || "[]");

        } catch (error) {

            console.error("Error leyendo datos de servicios:", error);

        }

        if (labels.length === 0) {
            console.warn("No hay datos de servicios para graficar");
            return;
        }

        const ctx3 = serviciosCanvas.getContext("2d");

        new Chart(ctx3, {

            type: "doughnut",

            data: {

                labels: labels,

                datasets: [{
                    data: valores,
                    backgroundColor: [
                        palette.blue,
                        palette.green,
                        palette.yellow,
                        palette.red,
                        palette.purple,
                        palette.teal
                    ],
                    borderWidth: 0
                }]

            },

            options: {

                responsive: true,
                maintainAspectRatio: false,

                cutout: "65%",

                plugins: {

                    legend: {
                        position: "bottom",
                        labels: {
                            padding: 20
                        }
                    },

                    tooltip: {
                        backgroundColor: "#111827",
                        padding: 12,
                        cornerRadius: 6
                    }

                },

                animation: {
                    duration: 900
                }

            }

        });



        // =========================
        // TOP SERVICIOS
        // =========================

        if (serviciosTopCanvas) {

            const ctx4 = serviciosTopCanvas.getContext("2d");

            new Chart(ctx4, {

                type: "bar",

                data: {

                    labels: labels,

                    datasets: [{
                        label: "Ventas",
                        data: valores,
                        backgroundColor: palette.blue,
                        borderRadius: 8,
                        borderSkipped: false
                    }]

                },

                options: {

                    responsive: true,
                    maintainAspectRatio: false,

                    plugins: {

                        legend: {
                            display: false
                        },

                        tooltip: {
                            backgroundColor: "#111827",
                            padding: 12
                        }

                    },

                    animation: {
                        duration: 900
                    },

                    scales: {

                        y: {
                            grid: {
                                color: palette.grid
                            },
                            beginAtZero: true
                        },

                        x: {
                            grid: {
                                display: false
                            }
                        }

                    }

                }

            });

        }

    }

});