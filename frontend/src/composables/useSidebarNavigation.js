/**
 * Shared sidebar navigation logic used by AppSidebar and MobileSidebar.
 *
 * Both sidebars share:
 *   - openOpsGate()  — SSO redirect to OpsGate; mobile additionally closes the
 *                      sidebar first (pass onBeforeOpen for that hook).
 *   - parseView()    — converts a view record to a SidebarLink props object.
 *   - getIcon()      — maps a route name to its icon component; desktop returns
 *                      the component directly while mobile wraps custom icons in
 *                      an h() node (pass wrapCustomIcons: true).
 */
import { h } from 'vue'
import { call, toast } from 'frappe-ui'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import ContactsIcon from '@/components/Icons/ContactsIcon.vue'
import OrganizationsIcon from '@/components/Icons/OrganizationsIcon.vue'
import AccountsIcon from '@/components/Icons/AccountsIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import PinIcon from '@/components/Icons/PinIcon.vue'

/**
 * @param {object}   [options]
 * @param {Function} [options.onBeforeOpen]  Called before the OpsGate window
 *                                           opens (e.g. close mobile sidebar).
 * @param {boolean}  [options.wrapCustomIcons] When true, custom icon components
 *                                           returned by getIcon are wrapped in
 *                                           h('div', { class: 'size-auto' }, icon)
 *                                           — required by the mobile sidebar renderer.
 */
export function useSidebarNavigation({
  onBeforeOpen,
  wrapCustomIcons = false,
} = {}) {
  async function openOpsGate() {
    if (onBeforeOpen) onBeforeOpen()
    try {
      const data = await call('crm.api.settings.get_opsgate_redirect_url')
      if (data?.redirect_url) {
        window.open(data.redirect_url, '_blank')
      } else {
        toast.error('OpsGate SSO failed: no redirect URL returned')
      }
    } catch {
      toast.error(
        'Could not sign you into OpsGate. Please contact your administrator.',
      )
    }
  }

  function parseView(views) {
    return views.map((view) => ({
      label: view.label,
      icon: getIcon(view.route_name, view.icon),
      to: {
        name: view.route_name,
        params: { viewType: view.type || 'list' },
        query: { view: view.name },
      },
    }))
  }

  function getIcon(routeName, icon) {
    // Custom icon passed directly from the view record
    if (icon) {
      return wrapCustomIcons ? h('div', { class: 'size-auto' }, icon) : icon
    }

    switch (routeName) {
      case 'Leads':
        return LeadsIcon
      case 'Deals':
        return DealsIcon
      case 'Contacts':
        return ContactsIcon
      case 'Organizations':
        return OrganizationsIcon
      case 'Accounts':
        return AccountsIcon
      case 'Notes':
        return NoteIcon
      case 'Call Logs':
        return PhoneIcon
      default:
        return PinIcon
    }
  }

  return { openOpsGate, parseView, getIcon }
}
