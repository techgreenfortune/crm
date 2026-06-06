import frappe
from frappe.core.doctype.user.user import STANDARD_USERS, User
from frappe.desk.notifications import clear_notifications


class CustomUser(User):
	def on_update(self):
		# Reimplements frappe's User.on_update WITHOUT the automatic Contact
		# creation. Contact is a custom, manually-managed doctype in this CRM,
		# so mirroring every User into a Contact is unwanted. Keep the rest of
		# the standard on_update behaviour intact.
		#
		# Forked from frappe v15.107.0 User.on_update — re-diff against upstream
		# on framework bumps so new behaviour isn't silently dropped.
		self.share_with_self()
		clear_notifications(user=self.name)
		frappe.clear_cache(user=self.name)
		now = frappe.flags.in_test or frappe.flags.in_install
		self.send_password_notification(getattr(self, "_User__new_password", None))

		if self.name not in STANDARD_USERS and not self.user_image:
			frappe.enqueue(
				"frappe.core.doctype.user.user.update_gravatar",
				name=self.name,
				now=now,
				enqueue_after_commit=True,
			)

		# Set user selected timezone
		if self.time_zone:
			frappe.defaults.set_default("time_zone", self.time_zone, self.name)

		if self.has_value_changed("enabled"):
			frappe.cache.delete_key("users_for_mentions")
			frappe.cache.delete_key("enabled_users")
		elif self.has_value_changed("allow_in_mentions") or self.has_value_changed("user_type"):
			frappe.cache.delete_key("users_for_mentions")
