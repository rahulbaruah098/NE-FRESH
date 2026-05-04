// /static/js/cart-global.js
// ================= GLOBAL CART COUNT =================
(function(){
  function updateCartCount(count){
    const badge = document.getElementById("cartCount");
    if (!badge) return;

    if (count > 0){
      badge.textContent = count;
      badge.style.display = "grid";
    } else {
      badge.style.display = "none";
    }
  }

  // Listen for custom cart events
  document.addEventListener("cart:updated", function(e){
    if (e.detail && typeof e.detail.count === "number"){
      updateCartCount(e.detail.count);
    }
  });

  // expose helper (optional)
  window.__updateCartCount = updateCartCount;
})();
