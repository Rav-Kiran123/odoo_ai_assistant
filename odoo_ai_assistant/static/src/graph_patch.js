import { patch } from "@web/core/utils/patch";
import { GraphRenderer } from "@web/views/graph/graph_renderer";
import { chartPixelRatio, OPENED_FROM_AI } from "./visualization";

// Odoo loads its graph view from the lazy backend bundle, so this patch lives there too.
patch(GraphRenderer.prototype, {
    prepareOptions() {
        const options = super.prepareOptions(...arguments);
        if (this.model.metaData.context?.[OPENED_FROM_AI]) {
            options.devicePixelRatio = chartPixelRatio();
        }
        return options;
    },
});
