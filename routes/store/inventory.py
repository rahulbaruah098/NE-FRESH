"""Store inventory route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/inventory', methods=['GET'], endpoint='store_inventory')
@login_required(role='store')
def store_inventory_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_inventory.html",
        user=u,
        store=store,
        **page_context
    )
