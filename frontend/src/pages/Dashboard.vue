<template>
  <div class="flex flex-col h-full overflow-hidden">
    <LayoutHeader>
      <template #left-header>
        <ViewBreadcrumbs routeName="Dashboard" />
      </template>
      <template #right-header>
        <Button
          v-if="!editing"
          :label="__('Refresh')"
          :iconLeft="LucideRefreshCcw"
          @click="dashboardItems.reload"
        />
        <Button
          v-if="!editing"
          :label="__('Download Leads')"
          :iconLeft="LucideDownload"
          @click="downloadLeads"
        />
        <Button
          v-if="!editing"
          :label="__('Download Calls')"
          :iconLeft="LucideDownload"
          @click="downloadCalls"
        />
        <Button
          v-if="!editing && isAdmin()"
          :label="__('Edit')"
          :iconLeft="LucidePenLine"
          @click="enableEditing"
        />
        <Button
          v-if="editing"
          :label="__('Chart')"
          iconLeft="plus"
          @click="showAddChartModal = true"
        />
        <Button
          v-if="editing && isAdmin()"
          :label="__('Reset to Default')"
          :iconLeft="LucideUndo2"
          @click="resetToDefault"
        />
        <Button v-if="editing" :label="__('Cancel')" @click="cancel" />
        <Button
          v-if="editing"
          variant="solid"
          :label="__('Save')"
          :disabled="!dirty"
          :loading="saveDashboard.loading"
          @click="save"
        />
      </template>
    </LayoutHeader>

    <div class="p-5 pb-2 flex items-center gap-4">
      <Dropdown
        v-if="!showDatePicker"
        v-model="preset"
        :options="options"
        class="form-control"
        :placeholder="__('Select Range')"
        :button="{
          label: __(preset),
          class:
            '!w-full justify-start [&>span]:mr-auto [&>svg]:text-ink-gray-5',
          variant: 'outline',
          iconRight: 'chevron-down',
          iconLeft: 'calendar',
        }"
      />
      <DateRangePicker
        v-else
        ref="datePickerRef"
        class="!w-48"
        :value="filters.period"
        variant="outline"
        :placeholder="__('Period')"
        :formatter="formatRange"
        @change="
          (v) =>
            updateFilter('period', v, () => {
              showDatePicker = false
              if (!v) {
                filters.period = getLastXDays()
                preset = 'Last 30 Days'
              } else {
                preset = formatter(v)
              }
            })
        "
      >
        <template #prefix>
          <LucideCalendar class="size-4 text-ink-gray-5 mr-2" />
        </template>
      </DateRangePicker>
      <!-- Multi-select user filter scoped to my hierarchy.
           Backend: crm.api.dashboard.get_visible_users returns downstream
           users only (admin sees all sales users).  Picker is hidden for
           leaf users with no reports — they only ever see their own data.
           Selecting a user expands to their full downstream subtree on the
           backend (selecting an ASM shows the ASM + their team's leads). -->
      <UserMultiSelect
        v-if="visibleUsers.data && visibleUsers.data.length > 0"
        class="w-52"
        :model-value="filters.users"
        :users="visibleUsers.data"
        :all-label="isAdmin() ? __('All leads') : __('My leads only')"
        :selected-label="
          filters.users.length > 1
            ? __('{0} users selected', [String(filters.users.length)])
            : ''
        "
        placement="bottom-end"
        @update:model-value="setUsers"
      />
    </div>

    <div class="w-full overflow-y-scroll">
      <DashboardGrid
        v-if="!dashboardItems.loading && dashboardItems.data"
        v-model="dashboardItems.data"
        class="pt-1"
        :editing="editing"
      />
    </div>
  </div>
  <AddChartModal
    v-if="showAddChartModal"
    v-model="showAddChartModal"
    v-model:items="dashboardItems.data"
  />
</template>

<script setup lang="ts">
import AddChartModal from '@/components/Dashboard/AddChartModal.vue'
import LucideRefreshCcw from '~icons/lucide/refresh-ccw'
import LucideUndo2 from '~icons/lucide/undo-2'
import LucidePenLine from '~icons/lucide/pen-line'
import LucideDownload from '~icons/lucide/download'
import DashboardGrid from '@/components/Dashboard/DashboardGrid.vue'
import UserMultiSelect from '@/components/UserMultiSelect.vue'
import ViewBreadcrumbs from '@/components/ViewBreadcrumbs.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import { usersStore } from '@/stores/users'
import { copy } from '@/utils'
import { getLastXDays, formatter, formatRange } from '@/utils/dashboard'
import {
  usePageMeta,
  createResource,
  DateRangePicker,
  Dropdown,
} from 'frappe-ui'
import { ref, reactive, computed, provide } from 'vue'

const { isAdmin } = usersStore()

const editing = ref(false)

const showDatePicker = ref(false)
const datePickerRef = ref(null)
const preset = ref('Last 30 Days')
const showAddChartModal = ref(false)

const filters = reactive({
  period: getLastXDays(),
  // Additive multi-select.  Default (empty) = own leads only (admin sees
  // every lead).  Each ticked user adds their downstream subtree to the
  // view on top of the caller's own leads.
  users: [] as string[],
})

// Reportees available to add via the picker.  Returns [] for leaf users
// (no reports → picker hides entirely) and for non-Sales-User callers.
// Admin sees all CRM Sales Users; non-admin sees their downstream MINUS
// themselves (self is always implicitly included).
const visibleUsers = createResource({
  url: 'crm.api.dashboard.get_visible_users',
  auto: true,
})

function setUsers(users: string[]) {
  filters.users = users
  dashboardItems.reload()
}

const fromDate = computed(() => {
  if (!filters.period) return null
  return filters.period.split(',')[0]
})

const toDate = computed(() => {
  if (!filters.period) return null
  return filters.period.split(',')[1]
})

function updateFilter(key: string, value: unknown, callback?: () => void) {
  filters[key] = value
  callback?.()
  dashboardItems.reload()
}

function buildExportParams() {
  const params = new URLSearchParams()
  if (fromDate.value) params.set('from_date', fromDate.value)
  if (toDate.value) params.set('to_date', toDate.value)
  if (filters.users.length) params.set('owners', JSON.stringify(filters.users))
  return params.toString()
}

function downloadLeads() {
  // Browser triggers the download; session cookie authenticates.
  // Backend gates by role (sales_user_only) and applies manager-vs-IC scoping.
  window.location.href = `/api/method/crm.api.dashboard.download_lead_export?${buildExportParams()}`
}

function downloadCalls() {
  window.location.href = `/api/method/crm.api.dashboard.download_calls_export?${buildExportParams()}`
}

const options = computed(() => [
  {
    group: 'Presets',
    hideLabel: true,
    items: [
      {
        label: __('Last 7 Days'),
        onClick: () => {
          preset.value = 'Last 7 Days'
          filters.period = getLastXDays(7)
          dashboardItems.reload()
        },
      },
      {
        label: __('Last 30 Days'),
        onClick: () => {
          preset.value = 'Last 30 Days'
          filters.period = getLastXDays(30)
          dashboardItems.reload()
        },
      },
      {
        label: __('Last 60 Days'),
        onClick: () => {
          preset.value = 'Last 60 Days'
          filters.period = getLastXDays(60)
          dashboardItems.reload()
        },
      },
      {
        label: __('Last 90 Days'),
        onClick: () => {
          preset.value = 'Last 90 Days'
          filters.period = getLastXDays(90)
          dashboardItems.reload()
        },
      },
    ],
  },
  {
    label: __('Custom Range'),
    onClick: () => {
      showDatePicker.value = true
      setTimeout(() => datePickerRef.value?.open(), 0)
      preset.value = 'Custom Range'
      filters.period = null // Reset period to allow custom date selection
    },
  },
])

const dashboardItems = createResource({
  url: 'crm.api.dashboard.get_dashboard',
  makeParams() {
    return {
      from_date: fromDate.value,
      to_date: toDate.value,
      // Send as JSON array — backend coerces via _coerce_users_arg.  Empty
      // list signals "no filter" so the backend uses the caller's full
      // visible scope (downstream subtree).
      users: filters.users.length ? JSON.stringify(filters.users) : null,
    }
  },
  auto: true,
})

const dirty = computed(() => {
  if (!editing.value) return false
  return JSON.stringify(dashboardItems.data) !== JSON.stringify(oldItems.value)
})

const oldItems = ref([])

provide('fromDate', fromDate)
provide('toDate', toDate)
provide('filters', filters)

function enableEditing() {
  editing.value = true
  oldItems.value = copy(dashboardItems.data)
}

function cancel() {
  editing.value = false
  dashboardItems.data = copy(oldItems.value)
}

const saveDashboard = createResource({
  url: 'frappe.client.set_value',
  method: 'POST',
  onSuccess: () => {
    dashboardItems.reload()
    editing.value = false
  },
})

function save() {
  const dashboardItemsCopy = copy(dashboardItems.data)

  dashboardItemsCopy.forEach((item: Record<string, unknown>) => {
    delete item.data
  })

  saveDashboard.submit({
    doctype: 'CRM Dashboard',
    name: 'Manager Dashboard',
    fieldname: 'layout',
    value: JSON.stringify(dashboardItemsCopy),
  })
}

function resetToDefault() {
  createResource({
    url: 'crm.api.dashboard.reset_to_default',
    auto: true,
    onSuccess: () => {
      dashboardItems.reload()
      editing.value = false
    },
  })
}

usePageMeta(() => {
  return { title: __('CRM Dashboard') }
})
</script>
