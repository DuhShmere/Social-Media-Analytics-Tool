document.addEventListener("DOMContentLoaded", async () => {
  const historySection = document.getElementById("post-history");

  if (!historySection) {
    return;
  }

  const statusElement = document.getElementById("history-status");
  const emptyElement = document.getElementById("history-empty");
  const chartGrid = document.getElementById("history-chart-grid");
  const formatNumber = new Intl.NumberFormat("en-US");

  function commonOptions(yAxisTitle, percentage = false) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: "index",
      },
      plugins: {
        legend: {
          display: false,
        },
      },
      scales: {
        x: {
          ticks: {
            autoSkip: true,
            maxTicksLimit: 8,
            maxRotation: 35,
          },
          title: {
            display: true,
            text: "Snapshot date",
          },
        },
        y: {
          beginAtZero: true,
          title: {
            display: true,
            text: yAxisTitle,
          },
          ticks: {
            callback(value) {
              return percentage
                ? `${value}%`
                : formatNumber.format(value);
            },
          },
        },
      },
    };
  }

  function createLineChart(canvasId, labels, values, label, color, options) {
    const canvas = document.getElementById(canvasId);

    return new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label,
            data: values,
            borderColor: color,
            backgroundColor: `${color}1f`,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.25,
            fill: true,
          },
        ],
      },
      options,
    });
  }

  try {
    const response = await fetch(historySection.dataset.apiUrl);

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const data = await response.json();
    const snapshots = data.snapshots;

    if (snapshots.length < 2) {
      statusElement.hidden = true;
      emptyElement.hidden = false;
      return;
    }

    const labels = snapshots.map((snapshot) => snapshot.label);

    createLineChart(
      "history-views-chart",
      labels,
      snapshots.map((snapshot) => snapshot.views),
      "Views",
      "#4f46e5",
      commonOptions("Views")
    );

    createLineChart(
      "history-interactions-chart",
      labels,
      snapshots.map((snapshot) => snapshot.total_interactions),
      "Interactions",
      "#0891b2",
      commonOptions("Interactions")
    );

    createLineChart(
      "history-engagement-chart",
      labels,
      snapshots.map((snapshot) => snapshot.engagement_rate),
      "Engagement rate",
      "#16a34a",
      commonOptions("Engagement rate (%)", true)
    );

    statusElement.hidden = true;
    chartGrid.hidden = false;
  } catch (error) {
    console.error(error);
    statusElement.textContent =
      "Performance history could not be loaded. Refresh the page.";
  }
});
