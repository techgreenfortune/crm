<template>
  <LayoutHeader v-if="account.doc">
    <header
      class="relative flex h-10.5 items-center justify-between gap-2 py-2.5 pl-2"
    >
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </header>
  </LayoutHeader>
  <div v-if="account.doc" class="flex flex-col h-full overflow-hidden">
    <FileUploader
      :validateFile="validateIsImageFile"
      @success="changeAccountImage"
    >
      <template #default="{ openFileSelector, error }">
        <div class="flex flex-col items-start justify-start gap-4 p-4">
          <div class="flex gap-4 items-center">
            <div class="group relative h-14.5 w-14.5">
              <Avatar
                size="3xl"
                class="h-14.5 w-14.5"
                :label="account.doc.account_name"
                :image="account.doc.account_logo"
              />
              <component
                :is="account.doc.account_logo ? Dropdown : 'div'"
                v-bind="
                  account.doc.account_logo
                    ? {
                        options: [
                          {
                            icon: 'upload',
                            label: account.doc.account_logo
                              ? __('Change Image')
                              : __('Upload Image'),
                            onClick: openFileSelector,
                          },
                          {
                            icon: 'trash-2',
                            label: __('Remove Image'),
                            onClick: () => changeAccountImage(''),
                          },
                        ],
                      }
                    : { onClick: openFileSelector }
                "
                class="!absolute bottom-0 left-0 right-0"
              >
                <div
                  class="z-1 absolute bottom-0 left-0 right-0 flex h-14 cursor-pointer items-center justify-center rounded-b-full bg-black bg-opacity-40 pt-5 opacity-0 duration-300 ease-in-out group-hover:opacity-100"
                  style="
                    -webkit-clip-path: inset(22px 0 0 0);
                    clip-path: inset(22px 0 0 0);
                  "
                >
                  <CameraIcon class="h-6 w-6 cursor-pointer text-white" />
                </div>
              </component>
            </div>
            <div class="flex flex-col gap-2 truncate">
              <div class="truncate text-lg font-medium text-ink-gray-9">
                {{ account.doc.name }}
              </div>
              <div class="flex items-center gap-1.5">
                <Button
                  v-if="canDelete"
                  :label="__('Delete')"
                  theme="red"
                  size="sm"
                  iconLeft="trash-2"
                  @click="deleteAccount"
                />
              </div>
              <ErrorMessage :message="__(error)" />
            </div>
          </div>
        </div>
      </template>
    </FileUploader>
    <Tabs
      v-model="tabIndex"
      as="div"
      :tabs="tabs"
      class="flex flex-1 overflow-auto flex-col [&_[role='tablist']]:gap-7.5 [&_[role='tablist']]:px-4 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-item="{ tab, selected }">
        <button
          v-if="tab.name !== 'Details'"
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
        <div v-if="tab.name == 'Details'">
          <div
            v-if="sections.data"
            class="flex flex-1 flex-col justify-between overflow-hidden"
          >
            <SidePanelLayout
              :sections="sections.data"
              doctype="CRM Account"
              :docname="account.doc.name"
              @reload="sections.reload"
            />
          </div>
        </div>
        <div
          v-if="tab.name !== 'Details'"
          class="flex justify-end px-4 pt-3 pb-1"
        >
          <Button
            v-if="tab.label === 'Leads'"
            size="sm"
            iconLeft="plus"
            :label="__('New Lead')"
            @click="showLeadModal = true"
          />
          <Button
            v-else-if="tab.label === 'Contacts'"
            size="sm"
            iconLeft="plus"
            :label="__('New Contact')"
            @click="showContactModal = true"
          />
        </div>
        <LeadsListView
          v-if="tab.label === 'Leads' && rows.length"
          :rows="rows"
          :columns="columns"
          :options="{ selectable: false, showTooltip: false }"
        />
        <ContactsListView
          v-if="tab.label === 'Contacts' && rows.length"
          :rows="rows"
          :columns="columns"
          :options="{ selectable: false, showTooltip: false }"
        />
        <div
          v-if="!rows.length && tab.name !== 'Details'"
          class="grid flex-1 place-items-center text-xl font-medium text-ink-gray-4"
        >
          <div class="flex flex-col items-center justify-center space-y-3">
            <component :is="tab.icon" class="!h-10 !w-10" />
            <div>{{ __('No {0} Found', [__(tab.label)]) }}</div>
          </div>
        </div>
      </template>
    </Tabs>
  </div>
  <LeadModal
    v-if="showLeadModal"
    v-model="showLeadModal"
    :defaults="{
      custom_account: props.accountId,
      custom_lead_type: 'Projects',
    }"
  />
  <ContactModal
    v-if="showContactModal"
    v-model="showContactModal"
    :contact="{ company_name: props.accountId }"
    :options="{ redirect: false, afterInsert: () => contacts.reload() }"
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Account'"
    :docname="props.accountId"
    name="Accounts"
  />
</template>

<script setup>
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import Icon from '@/components/Icon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import LeadsListView from '@/components/ListViews/LeadsListView.vue'
import ContactsListView from '@/components/ListViews/ContactsListView.vue'
import LeadModal from '@/components/Modals/LeadModal.vue'
import ContactModal from '@/components/Modals/ContactModal.vue'
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import ContactsIcon from '@/components/Icons/ContactsIcon.vue'
import { useDocument } from '@/data/document'
import { getSettings } from '@/stores/settings'
import { getMeta } from '@/stores/meta'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { getView } from '@/utils/view'
import { formatDate, timeAgo, validateIsImageFile } from '@/utils'
import {
  Breadcrumbs,
  Avatar,
  FileUploader,
  Dropdown,
  Tabs,
  createListResource,
  usePageMeta,
  createResource,
  toast,
} from 'frappe-ui'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { useTelemetry } from 'frappe-ui/frappe'
import { h, computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({
  accountId: { type: String, required: true },
})

const { brand } = getSettings()
const { getUser } = usersStore()
const { getLeadStatus } = statusesStore()
const { doctypeMeta } = getMeta('CRM Account')
const { capture } = useTelemetry()

const route = useRoute()

const {
  document: account,
  permissions,
  triggerOnRender,
} = useDocument('CRM Account', props.accountId)

const canDelete = computed(() => permissions.data?.permissions?.delete || false)

onMounted(async () => {
  if (account.doc) await triggerOnRender()
})

const breadcrumbs = computed(() => {
  let items = [{ label: __('Accounts'), route: { name: 'Accounts' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Account')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Accounts',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: {
      name: 'Account',
      params: { accountId: props.accountId },
    },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return account.doc?.[t] || props.accountId
})

usePageMeta(() => {
  return {
    title: title.value,
    icon: brand.favicon,
  }
})

async function changeAccountImage(file) {
  await account.setValue.submit({ account_logo: file?.file_url || '' })
}

async function deleteAccount() {
  showDeleteLinkedDocModal.value = true
}

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Account'],
  params: { doctype: 'CRM Account' },
  auto: true,
  transform: (data) => getParsedSections(data),
})

function getParsedSections(_sections) {
  return _sections.map((section) => {
    section.columns = section.columns.map((column) => {
      column.fields = column.fields.map((field) => {
        if (field.fieldname === 'address') {
          return {
            ...field,
            create: (value, close) => {
              showAddressModal()
              close()
            },
            edit: (address) => showAddressModal(address),
          }
        } else {
          return field
        }
      })
      return column
    })
    return section
  })
}

const showLeadModal = ref(false)
const showContactModal = ref(false)
const showDeleteLinkedDocModal = ref(false)
const tabIndex = ref(0)
const tabs = [
  {
    name: 'Details',
    label: __('Details'),
    icon: DetailsIcon,
  },
  {
    name: 'Leads',
    label: __('Leads'),
    icon: h(LeadsIcon, { class: 'h-4 w-4' }),
    count: computed(() => leads.data?.length),
  },
  {
    name: 'Contacts',
    label: __('Contacts'),
    icon: h(ContactsIcon, { class: 'h-4 w-4' }),
    count: computed(() => contacts.data?.length),
  },
]

const leads = createListResource({
  type: 'list',
  doctype: 'CRM Lead',
  cache: ['leads_account', props.accountId],
  fields: [
    'name',
    'lead_name',
    'image',
    'status',
    'lead_owner',
    'mobile_no',
    'email',
    'custom_account',
    'modified',
  ],
  filters: {
    custom_account: props.accountId,
  },
  orderBy: 'modified desc',
  pageLength: 20,
  auto: true,
})

const contacts = createListResource({
  type: 'list',
  doctype: 'Contact',
  cache: ['contacts_account', props.accountId],
  fields: [
    'name',
    'full_name',
    'image',
    'email_id',
    'mobile_no',
    'company_name',
    'modified',
  ],
  filters: {
    company_name: props.accountId,
  },
  orderBy: 'modified desc',
  pageLength: 20,
  auto: true,
})

const rows = computed(() => {
  // tab 0 = Details (no list), tab 1 = Leads, tab 2 = Contacts
  if (tabIndex.value === 0) return []
  let list = tabIndex.value === 1 ? leads : contacts

  if (!list.data) return []

  return list.data.map((row) => {
    return tabIndex.value === 1
      ? getLeadRowObject(row)
      : getContactRowObject(row)
  })
})

const columns = computed(() => {
  return tabIndex.value === 1 ? leadColumns : contactColumns
})

function getLeadRowObject(lead) {
  return {
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
    lead_owner: {
      label: lead.lead_owner && getUser(lead.lead_owner).full_name,
      ...(lead.lead_owner && getUser(lead.lead_owner)),
    },
    modified: {
      label: formatDate(lead.modified),
      timeAgo: __(timeAgo(lead.modified)),
    },
  }
}

function getContactRowObject(contact) {
  return {
    name: contact.name,
    full_name: {
      label: contact.full_name,
      image_label: contact.full_name,
      image: contact.image,
    },
    email: contact.email_id,
    mobile_no: contact.mobile_no,
    company_name: {
      label: contact.company_name,
      logo: account.doc?.account_logo,
    },
    modified: {
      label: formatDate(contact.modified),
      timeAgo: __(timeAgo(contact.modified)),
    },
  }
}

const leadColumns = [
  { label: __('Name'), key: 'lead_name', width: '16rem' },
  { label: __('Status'), key: 'status', width: '10rem' },
  { label: __('Email'), key: 'email', width: '12rem' },
  { label: __('Mobile No.'), key: 'mobile_no', width: '11rem' },
  { label: __('Lead Owner'), key: 'lead_owner', width: '10rem' },
  {
    label: __('Last Modified'),
    key: 'modified',
    width: '8rem',
    align: 'right',
  },
]

const contactColumns = [
  { label: __('Name'), key: 'full_name', width: '17rem' },
  { label: __('Email'), key: 'email', width: '12rem' },
  { label: __('Phone'), key: 'mobile_no', width: '12rem' },
  { label: __('Account'), key: 'company_name', width: '12rem' },
  { label: __('Last Modified'), key: 'modified', width: '8rem' },
]

const { showModal } = useDoctypeModal()

function showAddressModal(_address) {
  showModal({
    name: _address || null,
    doctype: 'Address',
    callbacks: {
      afterInsert: (d) => {
        capture('address_created')
        account.doc.address = d.name
        account.save.submit()
      },
    },
  })
}
</script>
