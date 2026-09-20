document.addEventListener("DOMContentLoaded", () => {
  const dashboard = document.getElementById(
    "analytics-dashboard"
  );

  if (!dashboard) {
    return;
  }

  const apiUrl = dashboard.dataset.apiUrl;
  const exportUrl = dashboard.dataset.exportUrl;

  const searchInput = document.getElementById(
    "post-search"
  );

  const accountSelect = document.getElementById(
    "account-select"
  );

  const platformSelect = document.getElementById(
    "platform-select"
  );

  const contentTypeSelect = document.getElementById(
    "content-type-select"
  );

  const startDateInput = document.getElementById(
    "start-date"
  );

  const endDateInput = document.getElementById(
    "end-date"
  );

  const resetButton = document.getElementById(
    "reset-filters"
  );

  const exportButton = document.getElementById(
    "export-csv"
  );

  const filterResults = document.getElementById(
    "filter-results"
  );

  const filterError = document.getElementById(
    "filter-error"
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

  const noMatchingPosts = document.getElementById(
    "no-matching-posts"
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

  const bestTimeHeading = document.getElementById(
    "best-time-heading"
  );

  const bestTimeDescription = document.getElementById(
    "best-time-description"
  );

  const bestTimeRate = document.getElementById(
    "best-time-rate"
  );

  const bestTimeRateValue = document.getElementById(
    "best-time-rate-value"
  );

  const postRows = Array.from(
    document.querySelectorAll(".post-row")
  );

  const formatNumber = new Intl.NumberFormat("en-US");

  let viewsChart = null;
  let engagementChart = null;
  let interactionsChart = null;
  let requestController = null;
  let searchTimer = null;

  function getFilters() {
    return {
      search: searchInput.value.trim().toLowerCase(),
      accountId: accountSelect.value,
      platform: platformSelect.value,
      contentType: contentTypeSelect.value,
      startDate: startDateInput.value,
      endDate: endDateInput.value,
    };
  }

  function datesAreValid(filters) {
    if (
      filters.startDate
      && filters.endDate
      && filters.startDate > filters.endDate
    ) {
      filterError.textContent =
        "The start date must be before the end date.";

      filterError.hidden = false;
      return false;
    }

    filterError.hidden = true;
    filterError.textContent = "";

    return true;
  }

  function addFilterParameters(url, filters) {
    if (filters.search) {
      url.searchParams.set(
        "search",
        filters.search
      );
    }

    if (filters.accountId) {
      url.searchParams.set(
        "account_id",
        filters.accountId
      );
    }

    if (filters.platform) {
      url.searchParams.set(
        "platform",
        filters.platform
      );
    }

    if (filters.contentType) {
      url.searchParams.set(
        "content_type",
        filters.contentType
      );
    }

    if (filters.startDate) {
      url.searchParams.set(
        "start_date",
        filters.startDate
      );
    }

    if (filters.endDate) {
      url.searchParams.set(
        "end_date",
        filters.endDate
      );
    }

    return url;
  }

  function buildRequestUrl(filters) {
    const requestUrl = new URL(
      apiUrl,
      window.location.origin
    );

    return addFilterParameters(
      requestUrl,
      filters
    );
  }

  function updateExportUrl(filters) {
    if (!exportButton || !exportUrl) {
      return;
    }

    const downloadUrl = new URL(
      exportUrl,
      window.location.origin
    );

    addFilterParameters(
      downloadUrl,
      filters
    );

    exportButton.href = downloadUrl.toString();
  }

  function filterTable(filters) {
    let visibleCount = 0;

    postRows.forEach((row) => {
      const matchesSearch = (
        !filters.search
        || row.dataset.caption.includes(filters.search)
      );

      const matchesAccount = (
        !filters.accountId
        || row.dataset.accountId === filters.accountId
      );

      const matchesPlatform = (
        !filters.platform
        || row.dataset.platform === filters.platform
      );

      const matchesContentType = (
        !filters.contentType
        || row.dataset.contentType === filters.contentType
      );

      const matchesStartDate = (
        !filters.startDate
        || row.dataset.date >= filters.startDate
      );

      const matchesEndDate = (
        !filters.endDate
        || row.dataset.date <= filters.endDate
      );

      const visible = (
        matchesSearch
        && matchesAccount
        && matchesPlatform
        && matchesContentType
        && matchesStartDate
        && matchesEndDate
      );

      row.hidden = !visible;

      if (visible) {
        visibleCount += 1;
      }
    });

    filterResults.textContent = (
      `Showing ${visibleCount} of ${postRows.length} posts`
    );

    if (noMatchingPosts) {
      noMatchingPosts.hidden = visibleCount !== 0;
    }
  }

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

  function updateBestTime(bestTime) {
    if (!bestTime || !bestTime.available) {
      const minimum = (
        bestTime?.minimum_sample_size || 3
      );

      bestTimeHeading.textContent =
        "More posting history is needed";

      bestTimeDescription.textContent =
        (
          `Add or import at least ${minimum} posts `
          + "published during the same weekday and "
          + "three-hour window."
        );

      bestTimeRate.hidden = true;
      return;
    }

    bestTimeHeading.textContent =
      `${bestTime.day}, ${bestTime.time_window}`;

    bestTimeDescription.textContent =
      (
        `Based on ${bestTime.sample_size} posts `
        + "published during this period."
      );

    bestTimeRateValue.textContent =
      `${bestTime.engagement_rate}%`;

    bestTimeRate.hidden = false;
  }

  function createViewsChart(data) {
    const canvas = document.getElementById(
      "views-chart"
    );

    viewsChart = new Chart(canvas, {
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
    const canvas = document.getElementById(
      "engagement-chart"
    );

    engagementChart = new Chart(canvas, {
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
    const canvas = document.getElementById(
      "interactions-chart"
    );

    const interactions = data.interactions;

    interactionsChart = new Chart(canvas, {
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
              interactions.likes,
              interactions.comments,
              interactions.shares,
              interactions.saves,
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
                  `${item.label}: `
                  + formatNumber.format(item.raw)
                );
              },
            },
          },
        },
      },
    });
  }

  async function loadAnalytics(filters) {
    if (requestController) {
      requestController.abort();
    }

    requestController = new AbortController();

    const requestUrl = buildRequestUrl(filters);

    statusElement.hidden = false;
    statusElement.textContent = "Loading analytics…";
    emptyElement.hidden = true;
    chartGrid.hidden = true;

    destroyCharts();

    try {
      const response = await fetch(
        requestUrl,
        {
          signal: requestController.signal,
        }
      );

      if (!response.ok) {
        throw new Error(
          `Request failed with status ${response.status}`
        );
      }

      const data = await response.json();

      updateSummary(data.summary);
      updateBestTime(data.best_time);

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
      if (error.name === "AbortError") {
        return;
      }

      console.error(error);

      statusElement.hidden = false;
      statusElement.textContent =
        "Analytics could not be loaded. Refresh the page.";
    }
  }

  function refreshDashboard() {
    const filters = getFilters();

    if (!datesAreValid(filters)) {
      return;
    }

    filterTable(filters);
    updateExportUrl(filters);
    loadAnalytics(filters);
  }

  function resetFilters() {
    searchInput.value = "";
    accountSelect.value = "";
    platformSelect.value = "";
    contentTypeSelect.value = "";
    startDateInput.value = "";
    endDateInput.value = "";

    refreshDashboard();
  }

  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);

    searchTimer = window.setTimeout(
      refreshDashboard,
      300
    );
  });

  accountSelect.addEventListener(
    "change",
    refreshDashboard
  );

  platformSelect.addEventListener(
    "change",
    refreshDashboard
  );

  contentTypeSelect.addEventListener(
    "change",
    refreshDashboard
  );

  startDateInput.addEventListener(
    "change",
    refreshDashboard
  );

  endDateInput.addEventListener(
    "change",
    refreshDashboard
  );

  resetButton.addEventListener(
    "click",
    resetFilters
  );

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

  refreshDashboard();
});