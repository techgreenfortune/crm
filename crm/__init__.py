__version__ = "1.70.0"
__title__ = "Frappe CRM"


def _install_field_visibility():
	from crm.permissions.field_visibility import install

	install()


try:
	_install_field_visibility()
except ImportError:
	# frappe not importable (packaging / tooling outside a bench)
	pass
