document.addEventListener("DOMContentLoaded", () => {
    const analyticsDashboard = document.getElementById(
      "analytics-dashboard"
    );
  
    if (!analyticsDashboard) {
      return;
    }
  
    const apiUrl = analyticsDashboard.dataset.apiUrl;
  
    const accountSelect = document.getElementById(
      "account-select"
    );
  
    const statusElement = document.getElementById(
      "analytics-status"
    );
  
    const emptyElement = document.getElementById(
      "analytics-empty"
    );
  
    const chartGrid = document.getElementById(
      "chart-grid"
    );
  
    const totalPostsElement = document.getElementById(
      "total-posts"
    );
  
    const totalViewsElement = document.getElementById(
      "total-views"
    );
  
    const totalInteractionsElement = document.getElementById(
      "total-interactions"
    );
  
    const engagementRateElement = document.getElementById(
      "engagement-rate"
    );
  
    let viewsChart = null;
    let engagementChart = null;
    let interactionsChart = null;
  
    const formatNumber = new Intl.NumberFormat("en-US");
  
    function destroyCharts() {
      if (viewsChart) {
        viewsChart.destroy();
        viewsChart = null;
      }
  
      if (engagementChart) {
        engagementChart.destroy();
        engagementChart = null;
      }
  
      if (interactionsChart) {
        interactionsChart.destroy();
        interactionsChart = null;
      }
    }
  
    function updateSummary(summary) {
      totalPostsElement.textContent = formatNumber.format(
        summary.total_posts
      );
  
      totalViewsElement.textContent = formatNumber.format(
        summary.total_views
      );
  
      totalInteractionsElement.textContent =
        formatNumber.format(
          summary.total_interactions
        );
  
      engagementRateElement.textContent =
        `${summary.engagement_rate}%`;
    }
  
    function createViewsChart(data) {
      const context = document.getElementById(
        "views-chart"
      );
  
      viewsChart = new Chart(context, {
        type: "bar",
  
        data: {
          labels: data.top_posts.map(
            (post) => post.short_title
          ),
  
          datasets: [
            {
              label: "Views",
              data: data.top_posts.map(
                (post) => post.views
              ),
              backgroundColor: "#4f46e5",
              borderRadius: 5,
            },
          ],
        },
  
        options: {
          indexAxis: "y",
          responsive: true,
          maintainAspectRatio: false,
  
          plugins: {
            legend: {
              display: false,
            },
  
            tooltip: {
              callbacks: {
                title(items) {
                  const index = items[0].dataIndex;
                  return data.top_posts[index].title;
                },
  
                label(item) {
                  return (
                    `Views: ${formatNumber.format(item.raw)}`
                  );
                },
              },
            },
          },
  
          scales: {
            x: {
              beginAtZero: true,
  
              title: {
                display: true,
                text: "Views",
              },
  
              ticks: {
                callback(value) {
                  return formatNumber.format(value);
                },
              },
            },
  
            y: {
              grid: {
                display: false,
              },
            },
          },
        },
      });
    }
  
    function createEngagementChart(data) {
      const context = document.getElementById(
        "engagement-chart"
      );
  
      engagementChart = new Chart(context, {
        type: "line",
  
        data: {
          labels: data.engagement_over_time.map(
            (post) => post.date
          ),
  
          datasets: [
            {
              label: "Engagement rate",
              data: data.engagement_over_time.map(
                (post) => post.engagement_rate
              ),
              borderColor: "#4f46e5",
              backgroundColor: "rgba(79, 70, 229, 0.12)",
              borderWidth: 2,
              pointRadius: 3,
              pointHoverRadius: 6,
              tension: 0.25,
              fill: true,
            },
          ],
        },
  
        options: {
          responsive: true,
          maintainAspectRatio: false,
  
          plugins: {
            legend: {
              display: false,
            },
  
            tooltip: {
              callbacks: {
                title(items) {
                  const index = items[0].dataIndex;
                  return data.engagement_over_time[
                    index
                  ].title;
                },
  
                afterTitle(items) {
                  const index = items[0].dataIndex;
                  return data.engagement_over_time[
                    index
                  ].date;
                },
  
                label(item) {
                  return `Engagement: ${item.raw}%`;
                },
              },
            },
          },
  
          scales: {
            x: {
              title: {
                display: true,
                text: "Post date",
              },
  
              ticks: {
                maxRotation: 45,
                minRotation: 0,
                autoSkip: true,
                maxTicksLimit: 8,
              },
            },
  
            y: {
              beginAtZero: true,
  
              title: {
                display: true,
                text: "Engagement rate (%)",
              },
  
              ticks: {
                callback(value) {
                  return `${value}%`;
                },
              },
            },
          },
        },
      });
    }
  
    function createInteractionsChart(data) {
      const context = document.getElementById(
        "interactions-chart"
      );
  
      const interactionData = data.interactions;
  
      interactionsChart = new Chart(context, {
        type: "doughnut",
  
        data: {
          labels: [
            "Likes",
            "Comments",
            "Shares",
            "Saves",
          ],
  
          datasets: [
            {
              data: [
                interactionData.likes,
                interactionData.comments,
                interactionData.shares,
                interactionData.saves,
              ],
  
              backgroundColor: [
                "#4f46e5",
                "#0891b2",
                "#ea580c",
                "#16a34a",
              ],
  
              borderWidth: 0,
            },
          ],
        },
  
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "62%",
  
          plugins: {
            legend: {
              position: "bottom",
            },
  
            tooltip: {
              callbacks: {
                label(item) {
                  return (
                    `${item.label}: ` +
                    formatNumber.format(item.raw)
                  );
                },
              },
            },
          },
        },
      });
    }
  
    async function loadAnalytics() {
      const accountId = accountSelect.value;
  
      const requestUrl = new URL(
        apiUrl,
        window.location.origin
      );
  
      if (accountId) {
        requestUrl.searchParams.set(
          "account_id",
          accountId
        );
      }
  
      statusElement.hidden = false;
      statusElement.textContent = "Loading analytics…";
      emptyElement.hidden = true;
      chartGrid.hidden = true;
  
      destroyCharts();
  
      try {
        const response = await fetch(requestUrl);
  
        if (!response.ok) {
          throw new Error(
            `Request failed with status ${response.status}`
          );
        }
  
        const data = await response.json();
  
        updateSummary(data.summary);
  
        if (data.summary.total_posts === 0) {
          statusElement.hidden = true;
          emptyElement.hidden = false;
          return;
        }
  
        createViewsChart(data);
        createEngagementChart(data);
        createInteractionsChart(data);
  
        statusElement.hidden = true;
        chartGrid.hidden = false;
      } catch (error) {
        console.error(error);
  
        statusElement.hidden = false;
        statusElement.textContent =
          "Analytics could not be loaded. Please refresh the page.";
      }
    }
  
    accountSelect.addEventListener(
      "change",
      loadAnalytics
    );
  
    loadAnalytics();
  });

  document.querySelectorAll(".delete-form").forEach(
    (form) => {
      form.addEventListener("submit", (event) => {
        const postTitle = form.dataset.postTitle;
  
        const confirmed = window.confirm(
          `Delete "${postTitle}"? This cannot be undone.`
        );
  
        if (!confirmed) {
          event.preventDefault();
        }
      });
    }
  );