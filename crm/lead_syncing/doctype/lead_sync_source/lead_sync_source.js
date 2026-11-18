// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lead Sync Source", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button(__("Refresh Forms"), () => {
      frm
        .call({ method: "refresh_forms", doc: frm.doc, freeze: true })
        .then(() => {
          frappe.show_alert({
            message: __("Pages and lead forms refreshed from Meta"),
            indicator: "green",
          });
        });
    });

    frm.add_custom_button(__("Sync Now"), () => {
      frm.call("sync_leads").then(() => {
        frappe.msgprint(__("Lead sync initiated."));
      });
    });
  },
});
