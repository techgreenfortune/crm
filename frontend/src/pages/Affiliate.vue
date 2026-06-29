<template>
  <LayoutHeader v-if="affiliate.doc">
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </template>
    <template #right-header>
      <CustomActions
        v-if="affiliate._actions?.length"
        :actions="affiliate._actions"
      />
    </template>
  </LayoutHeader>
  <div v-if="affiliate.doc" ref="parentRef" class="flex h-full">
    <Resizer
      :parent="$refs.parentRef"
      class="flex h-full flex-col overflow-hidden border-r"
    >
      <div class="border-b">
        <div class="flex flex-col items-start justify-start gap-4 p-5">
          <div class="flex gap-4 items-center">
            <Avatar
              size="3xl"
              class="h-15.5 w-15.5"
              :label="affiliate.doc.affiliate_name"
            />
            <div class="flex flex-col gap-2 truncate">
              <div class="truncate text-2xl font-medium text-ink-gray-9">
                <span>{{ affiliate.doc.name }}</span>
              </div>
              <div
                v-if="affiliate.doc.email"
                class="flex items-center gap-1.5 text-base text-ink-gray-8"
              >
                <span>{{ affiliate.doc.email }}</span>
              </div>
              <div
                v-if="affiliate.doc.status"
                class="flex items-center gap-1.5 text-sm text-ink-gray-7"
              >
                <Badge
                  :theme="affiliate.doc.status === 'Active' ? 'green' : 'gray'"
                  size="sm"
                  >{{ __(affiliate.doc.status) }}</Badge
                >
              </div>
            </div>
          </div>
          <div class="flex gap-1.5">
            <Button
              v-if="canDelete"
              :label="__('Delete')"
              theme="red"
              size="sm"
              iconLeft="trash-2"
              @click="deleteAffiliate()"
            />
          </div>
        </div>
      </div>
      <div
        v-if="sections.data"
        class="flex flex-1 flex-col justify-between overflow-hidden"
      >
        <SidePanelLayout
          :sections="sections.data"
          doctype="CRM Affiliate"
          :docname="affiliate.doc.name"
          @reload="sections.reload"
          @beforeFieldChange="beforeFieldChange"
        />
      </div>
    </Resizer>
    <Tabs
      v-model="tabIndex"
      as="div"
      :tabs="tabs"
      class="flex flex-1 overflow-hidden flex-col [&_[role='tablist']]:gap-7.5 [&_[role='tablist']]:px-5 [&_[role='tablist']::-webkit-scrollbar]:h-0 [&_[role='tablist']]:min-h-[45px] [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-item="{ tab, selected }">
        <button
          class="group flex items-center gap-2 border-b border-transparent py-2.5 text-base text-ink-gray-5 duration-300 ease-in-out hover:text-ink-gray-9"
          :class="{ 'text-ink-gray-9': selected }"
        >
          <component :is="tab.icon" v-if="tab.icon" class="h-5" />
          {{ __(tab.label) }}
          <Badge
            class="group-hover:bg-surface-gray-7"
            :class="[selected ? 'bg-surface-gray-7' : 'bg-gray-600']"
            variant="solid"
            theme="gray"
            size="sm"
          >
            {{ tab.count }}
          </Badge>
        </button>
      </template>
      <template #tab-panel="{ tab }">
        <div class="flex justify-end px-5 pt-3 pb-1">
          <Button
            v-if="tab.label === 'Leads'"
            size="sm"
            iconLeft="plus"
            :label="__('New Lead')"
            @click="showLeadModal = true"
          />
        </div>
        <LeadsListView
          v-if="tab.label === 'Leads' && rows.length"
          :rows="rows"
          :columns="columns"
          :options="{ selectable: false, showTooltip: false }"
        />
        <EmptyState
          v-if="!rows.length"
          :icon="tab.icon"
          :name="__(tab.label)"
        />
      </template>
    </Tabs>
  </div>
  <LeadModal
    v-if="showLeadModal"
    v-model="showLeadModal"
    :defaults="{
      custom_is_affiliate_lead: 1,
      custom_affiliate: props.affiliateId,
      custom_affiliate_commission_pct: affiliate.doc?.default_commission_pct,
    }"
  />
  <ErrorPage
    v-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Affiliate'"
    :docname="props.affiliateId"
    name="Affiliates"
  />
</template>

<script setup>
import ErrorPage from '@/components/ErrorPage.vue'
import Resizer from '@/components/Resizer.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import Icon from '@/components/Icon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import LeadsListView from '@/components/ListViews/LeadsListView.vue'
import LeadModal from '@/components/Modals/LeadModal.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import CustomActions from '@/components/CustomActions.vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'
import { useDocument } from '@/data/document'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { getMeta } from '@/stores/meta'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { getView } from '@/utils/view'
import { formatDate, timeAgo, setupCustomizations } from '@/utils'
import {
  Breadcrumbs,
  Avatar,
  Badge,
  Tabs,
  createListResource,
  usePageMeta,
  createResource,
  toast,
  call,
} from 'frappe-ui'
import { useTelemetry } from 'frappe-ui/frappe'
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  affiliateId: { type: String, required: true },
})

const { brand } = getSettings()
const { $dialog, $socket } = globalStore()
const { getUser } = usersStore()
const { getLeadStatus } = statusesStore()
const { doctypeMeta } = getMeta('CRM Affiliate')
const { capture } = useTelemetry()

const route = useRoute()
const router = useRouter()

const errorTitle = ref('')
const errorMessage = ref('')

const showDeleteLinkedDocModal = ref(false)
const showLeadModal = ref(false)

const {
  document: affiliate,
  permissions,
  scripts,
  triggerOnRender,
} = useDocument('CRM Affiliate', props.affiliateId)

const canDelete = computed(() => permissions.data?.permissions?.delete || false)

onMounted(async () => {
  if (affiliate.doc) await triggerOnRender()
})

const breadcrumbs = computed(() => {
  let items = [{ label: __('Affiliates'), route: { name: 'Affiliates' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Affiliate')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Affiliates',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: {
      name: 'Affiliate',
      params: { affiliateId: props.affiliateId },
    },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return affiliate.doc?.[t] || props.affiliateId
})

usePageMeta(() => {
  return {
    title: title.value,
    icon: brand.favicon,
  }
})

async function deleteAffiliate() {
  showDeleteLinkedDocModal.value = true
}

function beforeFieldChange(data) {
  if (Object.hasOwn(data ?? {}, 'affiliate_name')) {
    call('frappe.client.rename_doc', {
      doctype: 'CRM Affiliate',
      old_name: props.affiliateId,
      new_name: data.affiliate_name,
    }).then(() => {
      router.push({
        name: 'Affiliate',
        params: { affiliateId: data.affiliate_name },
      })
    })
  } else {
    affiliate.save.submit()
  }
}

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Affiliate'],
  params: { doctype: 'CRM Affiliate' },
  auto: true,
})

const tabIndex = ref(0)
const tabs = [
  {
    label: 'Leads',
    icon: LeadsIcon,
    count: computed(() => leads.data?.length),
  },
]

// Leads associated with this affiliate — filtered via the custom_affiliate
// link field on CRM Lead (set when the lead is tagged as an affiliate lead).
const leads = createListResource({
  type: 'list',
  doctype: 'CRM Lead',
  cache: ['leads_affiliate', props.affiliateId],
  fields: [
    'name',
    'lead_name',
    'image',
    'status',
    'lead_owner',
    'mobile_no',
    'email',
    'custom_affiliate',
    'custom_affiliate_commission_pct',
    'modified',
  ],
  filters: {
    custom_affiliate: props.affiliateId,
  },
  orderBy: 'modified desc',
  pageLength: 20,
  auto: true,
})

const rows = computed(() => {
  if (!leads.data) return []
  return leads.data.map((lead) => ({
    name: lead.name,
    lead_name: {
      label: lead.lead_name,
      image_label: lead.lead_name,
      image: lead.image,
    },
    status: {
      label: lead.status,
      color: getLeadStatus(lead.status)?.color,
    },
    email: lead.email,
    mobile_no: lead.mobile_no,
    custom_affiliate_commission_pct: lead.custom_affiliate_commission_pct,
    lead_owner: {
      label: lead.lead_owner && getUser(lead.lead_owner).full_name,
      ...(lead.lead_owner && getUser(lead.lead_owner)),
    },
    modified: {
      label: formatDate(lead.modified),
      timeAgo: __(timeAgo(lead.modified)),
    },
  }))
})

const columns = computed(() => [
  { label: __('Name'), key: 'lead_name', width: '14rem' },
  { label: __('Status'), key: 'status', width: '10rem' },
  { label: __('Commission %'), key: 'custom_affiliate_commission_pct', width: '8rem' },
  { label: __('Email'), key: 'email', width: '12rem' },
  { label: __('Mobile No.'), key: 'mobile_no', width: '11rem' },
  { label: __('Lead Owner'), key: 'lead_owner', width: '10rem' },
  { label: __('Last Modified'), key: 'modified', width: '8rem', align: 'right' },
])

watch(
  () => affiliate.doc,
  async (_doc) => {
    if (scripts.data?.length) {
      let s = await setupCustomizations(scripts.data, {
        doc: _doc,
        $dialog,
        $socket,
        router,
        toast,
        updateField: affiliate.setValue.submit,
        createToast: toast.create,
        deleteDoc: deleteAffiliate,
        call,
      })
      affiliate._actions = s.actions || []
    }
  },
  { once: true },
)
</script>
