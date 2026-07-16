function showTab(name) {
    document.querySelectorAll(".tab-panel").forEach((el) => (el.style.display = "none"));
    document.querySelectorAll(".tab-btn").forEach((el) => el.classList.remove("active"));
    document.getElementById("tab-" + name).style.display = "";
    event.target.classList.add("active");
}
