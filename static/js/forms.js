// Shows that a form is working and blocks double submissions.
// Forms opt in with data-loading-text="Saving…".
document.addEventListener("DOMContentLoaded", () => {
  const forms = document.querySelectorAll("form[data-loading-text]");

  function startLoading(form, button) {
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");

    button.dataset.originalHtml = button.innerHTML;
    button.textContent = form.dataset.loadingText;
    button.disabled = true;
  }

  function resetForm(form) {
    const button = form.querySelector('button[type="submit"]');

    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");

    if (button && button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      button.disabled = false;
    }
  }

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      // Another handler, such as a confirmation, may have cancelled it.
      if (event.defaultPrevented) {
        return;
      }

      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      const button = form.querySelector('button[type="submit"]');

      if (button) {
        startLoading(form, button);
      }
    });
  });

  // The Back button can restore this page from the browser cache with
  // the button still disabled, so reset every form when that happens.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      forms.forEach(resetForm);
    }
  });
});
