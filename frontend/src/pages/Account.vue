<template>
  <LayoutHeader v-if="account.doc">
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </template>
    <template #right-header>
      <CustomActions
        v-if="account._actions?.length"
        :actions="account._actions"
      />
    </template>
  </LayoutHeader>
  <div v-if="account.doc" ref="parentRef" class="flex h-full">
    <Resizer
      :parent="$refs.parentRef"
      class="flex h-full flex-col overflow-hidden border-r"
    >
      <div class="border-b">
        <FileUploader
          :validateFile="validateIsImageFile"
          @success="changeAccountImage"
        >
          <template #default="{ openFileSelector, error }">
            <div class="flex flex-col items-start justify-start gap-4 p-5">
              <div class="flex gap-4 items-center">
                <div class="group relative h-15.5 w-15.5">
                  <Avatar
                    size="3xl"
                    class="h-15.5 w-15.5"
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
                  <div class="truncate text-2xl font-medium text-ink-gray-9">
                    <span>{{ account.doc.name }}</span>
                  </div>
                  <div
                    v-if="account.doc.website"
                    class="flex items-center gap-1.5 text-base text-ink-gray-8"
                  >
                    <WebsiteIcon class="size-4" />
                    <span>{{ website(account.doc.website) }}</span>
                  </div>
                  <ErrorMessage :message="__(error)" />
                </div>
              </div>
              <div class="flex gap-1.5">
                <Button
                  v-if="canDelete"
                  :label="__('Delete')"
                  theme="red"
                  size="sm"
                  iconLeft="trash-2"
                  @click="deleteAccount()"
                />
                <Button
                  v-if="account.doc.website"
                  :tooltip="__('Open Website')"
                  icon="link"
                  @click="openWebsite"
                />
              </div>
            </div>
          </template>
        </FileUploader>
      </div>
      <div
        v-if="sections.data"
        class="flex flex-1 flex-col justify-between overflow-hidden"
      >
        <SidePanelLayout
          :sections="sections.data"
          doctype="CRM Account"
          :docname="account.doc.name"
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
  <ErrorPage
    v-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
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
import ErrorPage from '@/components/ErrorPage.vue'
import Resizer from '@/components/Resizer.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import Icon from '@/components/Icon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import LeadsListView from '@/components/ListViews/LeadsListView.vue'
import ContactsListView from '@/components/ListViews/ContactsListView.vue'
import LeadModal from '@/components/Modals/LeadModal.vue'
import ContactModal from '@/components/Modals/ContactModal.vue'
import WebsiteIcon from '@/components/Icons/WebsiteIcon.vue'
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import ContactsIcon from '@/components/Icons/ContactsIcon.vue'
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import CustomActions from '@/components/CustomActions.vue'
import { useDocument } from '@/data/document'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { getMeta } from '@/stores/meta'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { getView } from '@/utils/view'
import {
  formatDate,
  timeAgo,
  validateIsImageFile,
  setupCustomizations,
  openWebsite as openExternalWebsite,
} from '@/utils'
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
  call,
} from 'frappe-ui'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { useTelemetry } from 'frappe-ui/frappe'
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  accountId: { type: String, required: true },
})

const { brand } = getSettings()
const { $dialog, $socket } = globalStore()
const { getUser } = usersStore()
const { getLeadStatus } = statusesStore()
const { doctypeMeta } = getMeta('CRM Account')
const { capture } = useTelemetry()

const route = useRoute()
const router = useRouter()

const errorTitle = ref('')
const errorMessage = ref('')

const showDeleteLinkedDocModal = ref(false)
const showLeadModal = ref(false)
const showContactModal = ref(false)

const {
  document: account,
  permissions,
  scripts,
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

async function deleteAccount() {
  showDeleteLinkedDocModal.value = true
}

function changeAccountImage(file) {
  account.setValue.submit({
    account_logo: file?.file_url || null,
  })
}

function beforeFieldChange(data) {
  if (Object.hasOwn(data ?? {}, 'account_name')) {
    call('frappe.client.rename_doc', {
      doctype: 'CRM Account',
      old_name: props.accountId,
      new_name: data.account_name,
    }).then(() => {
      router.push({
        name: 'Account',
        params: { accountId: data.account_name },
      })
    })
  } else {
    account.save.submit()
  }
}

function website(url) {
  return url && url.replace(/^(?:https?:\/\/)?(?:www\.)?/i, '')
}

function openWebsite() {
  if (!account.doc.website) {
    toast.error(__('No Website Found'))
    return
  }
  openExternalWebsite(account.doc.website)
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

const tabIndex = ref(0)
const tabs = [
  {
    label: 'Leads',
    icon: LeadsIcon,
    count: computed(() => leads.data?.length),
  },
  {
    label: 'Contacts',
    icon: ContactsIcon,
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
  let list = !tabIndex.value ? leads : contacts

  if (!list.data) return []

  return list.data.map((row) => {
    return !tabIndex.value ? getLeadRowObject(row) : getContactRowObject(row)
  })
})

const columns = computed(() => {
  return tabIndex.value === 0 ? leadColumns : contactColumns
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
  {
    label: __('Name'),
    key: 'lead_name',
    width: '16rem',
  },
  {
    label: __('Status'),
    key: 'status',
    width: '10rem',
  },
  {
    label: __('Email'),
    key: 'email',
    width: '12rem',
  },
  {
    label: __('Mobile No.'),
    key: 'mobile_no',
    width: '11rem',
  },
  {
    label: __('Lead Owner'),
    key: 'lead_owner',
    width: '10rem',
  },
  {
    label: __('Last Modified'),
    key: 'modified',
    width: '8rem',
    align: 'right',
  },
]

const contactColumns = [
  {
    label: __('Name'),
    key: 'full_name',
    width: '17rem',
  },
  {
    label: __('Email'),
    key: 'email',
    width: '12rem',
  },
  {
    label: __('Phone'),
    key: 'mobile_no',
    width: '12rem',
  },
  {
    label: __('Account'),
    key: 'company_name',
    width: '12rem',
  },
  {
    label: __('Last Modified'),
    key: 'modified',
    width: '8rem',
  },
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

watch(
  () => account.doc,
  async (_doc) => {
    if (scripts.data?.length) {
      let s = await setupCustomizations(scripts.data, {
        doc: _doc,
        $dialog,
        $socket,
        router,
        toast,
        updateField: account.setValue.submit,
        createToast: toast.create,
        deleteDoc: deleteAccount,
        call,
      })
      account._actions = s.actions || []
    }
  },
  { once: true },
)
</script>
