<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </template>
    <template v-if="!errorTitle" #right-header>
      <CustomActions
        v-if="document._actions?.length"
        :actions="document._actions"
      />
      <CustomActions
        v-if="document.actions?.length"
        :actions="document.actions"
      />
      <AssignTo v-model="assignees.data" doctype="CRM Lead" :docname="leadId" />
      <Dropdown
        v-if="doc && document.statuses"
        :options="statuses"
        placement="right"
      >
        <template #default="{ open }">
          <Button
            v-if="doc.status"
            :label="statusLabel(doc.status)"
            :iconRight="open ? 'chevron-up' : 'chevron-down'"
          >
            <template #prefix>
              <IndicatorIcon :class="getLeadStatus(doc.status).color" />
            </template>
          </Button>
        </template>
      </Dropdown>
      <Dropdown
        v-if="doc.lead_status && doc.lead_status !== 'Active'"
        :options="engagementStatuses"
        placement="right"
      >
        <template #default="{ open }">
          <Button
            :label="doc.lead_status"
            :iconRight="open ? 'chevron-up' : 'chevron-down'"
          >
            <template #prefix>
              <IndicatorIcon
                :class="getLeadEngagementStatus(doc.lead_status)?.color"
              />
            </template>
          </Button>
        </template>
      </Dropdown>
      <Tooltip
        v-if="canShowCreateProject"
        :text="
          projectFieldsReady
            ? __('Create project and archive this lead')
            : __(
                'Fill all project fields (category, configuration, site address, site pincode) before handing off.',
              )
        "
      >
        <Button
          variant="solid"
          :label="__('Create Project')"
          iconLeft="briefcase"
          :loading="createProjectResource.loading"
          :disabled="!projectFieldsReady"
          @click="triggerCreateProject"
        />
      </Tooltip>
      <!-- disabled: convert-to-deal flow retired
      <Button
        :label="__('Convert to Deal')"
        variant="solid"
        @click="showConvertToDealModal = true"
      />
      -->
    </template>
  </LayoutHeader>
  <div v-if="doc.name" class="flex h-full overflow-hidden">
    <Tabs
      v-model="tabIndex"
      :tabs="tabs"
      class="flex flex-1 overflow-hidden flex-col [&_[role='tab']]:px-0 [&_[role='tab']]:shrink-0 [&_[role='tablist']]:px-5 [&_[role='tablist']::-webkit-scrollbar]:h-0 [&_[role='tablist']]:min-h-[45px] [&_[role='tablist']]:gap-7.5 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-panel>
        <Activities
          ref="activities"
          v-model:reload="reload"
          v-model:tabIndex="tabIndex"
          doctype="CRM Lead"
          :docname="leadId"
          :tabs="tabs"
          @beforeSave="beforeStatusChange"
          @afterSave="reloadResources"
        />
      </template>
    </Tabs>
    <Resizer class="flex flex-col justify-between border-l" side="right">
      <div
        class="flex h-[45px] cursor-copy items-center border-b px-5 py-2.5 text-lg font-medium text-ink-gray-9"
        @click="copyToClipboard(leadId)"
      >
        {{ __(leadId) }}
      </div>
      <div
        v-if="isLocked"
        class="mx-5 mt-3 rounded border border-outline-amber-2 bg-surface-amber-1 px-3 py-2 text-xs text-ink-amber-9"
      >
        {{
          __(
            'This lead is Won (C4 + Won) and is locked for edits. Contact a System Manager to unlock.',
          )
        }}
      </div>
      <FileUploader
        :validateFile="validateIsImageFile"
        @success="(file) => updateField('image', file.file_url)"
      >
        <template #default="{ openFileSelector }">
          <div class="flex items-center justify-start gap-5 border-b p-5">
            <div class="group relative size-12">
              <Avatar
                size="3xl"
                class="size-12"
                :label="title"
                :image="doc.image"
              />
              <component
                :is="doc.image ? Dropdown : 'div'"
                v-bind="
                  doc.image
                    ? {
                        options: [
                          {
                            icon: 'upload',
                            label: doc.image
                              ? __('Change Image')
                              : __('Upload Image'),
                            onClick: openFileSelector,
                          },
                          {
                            icon: 'trash-2',
                            label: __('Remove Image'),
                            onClick: () => updateField('image', ''),
                          },
                        ],
                      }
                    : { onClick: openFileSelector }
                "
                class="!absolute bottom-0 left-0 right-0"
              >
                <div
                  class="z-1 absolute bottom-0.5 left-0 right-0.5 flex h-9 cursor-pointer items-center justify-center rounded-b-full bg-black bg-opacity-40 pt-3 opacity-0 duration-300 ease-in-out group-hover:opacity-100"
                  style="
                    -webkit-clip-path: inset(12px 0 0 0);
                    clip-path: inset(12px 0 0 0);
                  "
                >
                  <CameraIcon class="size-4 cursor-pointer text-white" />
                </div>
              </component>
            </div>
            <div class="flex flex-col gap-2.5 truncate">
              <Tooltip :text="doc.lead_name || __('Set First Name')">
                <div class="truncate text-2xl font-medium text-ink-gray-9">
                  {{ title }}
                </div>
              </Tooltip>
              <div class="flex gap-1.5">
                <Button
                  v-if="callEnabled"
                  :tooltip="__('Make a Call')"
                  :icon="PhoneIcon"
                  @click="
                    () =>
                      doc.mobile_no
                        ? makeCall(doc.mobile_no, {
                            reference_doctype: 'CRM Lead',
                            reference_docname: doc.name,
                          })
                        : toast.error(
                            __('Please set a mobile number to make calls'),
                          )
                  "
                />

                <Button
                  :tooltip="__('Send an Email')"
                  :icon="Email2Icon"
                  @click="
                    doc.email
                      ? openEmailBox()
                      : toast.error(
                          __('Please set an email address to send emails'),
                        )
                  "
                />
                <Button
                  :tooltip="__('Go to Website')"
                  :icon="LinkIcon"
                  @click="
                    doc.website
                      ? openWebsite(doc.website)
                      : toast.error(__('Please set a website to visit'))
                  "
                />

                <Button
                  :tooltip="__('Attach a File')"
                  :icon="AttachmentIcon"
                  @click="showFilesUploader = true"
                />

                <Button
                  v-if="canDelete"
                  :tooltip="__('Delete')"
                  variant="subtle"
                  theme="red"
                  icon="trash-2"
                  @click="deleteLead"
                />
              </div>
              <ErrorMessage :message="__(error)" />
            </div>
          </div>
        </template>
      </FileUploader>
      <SLASection
        v-if="doc.sla_status"
        v-model="doc"
        @updateField="updateField"
      />
      <div
        v-if="sections.data"
        class="flex flex-1 flex-col justify-between overflow-hidden"
      >
        <SidePanelLayout
          :sections="sections.data"
          :addContact="addContact"
          doctype="CRM Lead"
          :docname="leadId"
          @reload="sections.reload"
          @beforeFieldChange="beforeStatusChange"
          @afterFieldChange="reloadResources"
        >
          <template #default="{ section }">
            <div
              v-if="section.name == 'contacts_section'"
              class="contacts-area"
            >
              <div
                v-if="leadContacts?.loading && leadContacts?.data?.length == 0"
                class="flex min-h-20 flex-1 items-center justify-center gap-3 text-base text-ink-gray-4"
              >
                <LoadingIndicator class="h-4 w-4" />
                <span>{{ __('Loading...') }}</span>
              </div>
              <div
                v-for="(contact, i) in leadContacts.data"
                v-else-if="leadContacts?.data?.length"
                :key="contact.name"
              >
                <div class="px-2 pb-2.5" :class="[i == 0 ? 'pt-5' : 'pt-2.5']">
                  <Section :opened="contact.opened">
                    <template #header="{ opened, toggle }">
                      <div
                        class="flex cursor-pointer items-center justify-between gap-2 pr-1 text-base leading-5 text-ink-gray-7"
                      >
                        <div
                          class="flex h-7 items-center gap-2 truncate"
                          @click="toggle()"
                        >
                          <Avatar
                            :label="contact.full_name"
                            :image="contact.image"
                            size="md"
                          />
                          <div class="truncate">
                            {{ contact.full_name }}
                          </div>
                          <Badge
                            v-if="contact.is_primary"
                            class="ml-2"
                            variant="outline"
                            :label="__('Primary')"
                            theme="green"
                          />
                        </div>
                        <div class="flex items-center">
                          <Dropdown :options="contactOptions(contact)">
                            <Button
                              icon="more-horizontal"
                              class="text-ink-gray-5"
                              variant="ghost"
                            />
                          </Dropdown>
                          <Button
                            variant="ghost"
                            :tooltip="__('View Contact')"
                            :icon="ArrowUpRightIcon"
                            @click="
                              router.push({
                                name: 'Contact',
                                params: { contactId: contact.name },
                              })
                            "
                          />
                          <Button
                            variant="ghost"
                            class="transition-all duration-300 ease-in-out"
                            :class="{ 'rotate-90': opened }"
                            icon="chevron-right"
                            @click="toggle()"
                          />
                        </div>
                      </div>
                    </template>
                    <div class="flex flex-col gap-1.5 text-base">
                      <div
                        v-if="contact.email"
                        class="flex items-center gap-3 pb-1.5 pl-1 pt-4 text-ink-gray-8"
                      >
                        <Email2Icon class="h-4 w-4" />
                        {{ contact.email }}
                      </div>
                      <div
                        v-if="contact.mobile_no"
                        class="flex items-center gap-3 p-1 py-1.5 text-ink-gray-8"
                      >
                        <PhoneIcon class="h-4 w-4" />
                        {{ contact.mobile_no }}
                      </div>
                      <div
                        v-if="!contact.email && !contact.mobile_no"
                        class="flex items-center justify-center py-4 text-sm text-ink-gray-4"
                      >
                        {{ __('No Details Added') }}
                      </div>
                    </div>
                  </Section>
                </div>
                <div
                  v-if="i != leadContacts.data.length - 1"
                  class="mx-2 h-px border-t border-outline-gray-modals"
                />
              </div>
              <div
                v-else
                class="flex h-20 items-center justify-center text-base text-ink-gray-5"
              >
                {{ __('No Contacts Added') }}
              </div>
            </div>
          </template>
        </SidePanelLayout>
      </div>
    </Resizer>
  </div>
  <ErrorPage
    v-else-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
  />
  <!-- disabled: convert-to-deal flow retired
  <ConvertToDealModal
    v-if="showConvertToDealModal"
    v-model="showConvertToDealModal"
    :lead="doc"
  />
  -->
  <FilesUploader
    v-model="showFilesUploader"
    doctype="CRM Lead"
    :docname="leadId"
    @after="
      () => {
        activities?.all_activities?.reload()
        changeTabTo('attachments')
      }
    "
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Lead'"
    :docname="leadId"
    name="Leads"
  />
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    doctype="CRM Lead"
    :document="document"
  />
  <FabricatorRoutingReasonModal
    v-if="showFabricatorRoutingReasonModal"
    v-model="showFabricatorRoutingReasonModal"
    doctype="CRM Lead"
    :document="document"
  />
  <ContactModal
    v-if="showContactModal"
    v-model="showContactModal"
    :contact="_contact"
    :options="{
      redirect: false,
      afterInsert: (_doc) => addContact(_doc.name),
    }"
  />
</template>
<script setup>
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import ErrorPage from '@/components/ErrorPage.vue'
import Icon from '@/components/Icon.vue'
import Resizer from '@/components/Resizer.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import LinkIcon from '@/components/Icons/LinkIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import DocumentIcon from '@/components/Icons/DocumentIcon.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import FabricatorRoutingReasonModal from '@/components/Modals/FabricatorRoutingReasonModal.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Activities from '@/components/Activities/Activities.vue'
import AssignTo from '@/components/AssignTo.vue'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import SLASection from '@/components/SLASection.vue'
import Section from '@/components/Section.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import CustomActions from '@/components/CustomActions.vue'
import ArrowUpRightIcon from '@/components/Icons/ArrowUpRightIcon.vue'
import SuccessIcon from '@/components/Icons/SuccessIcon.vue'
import ContactModal from '@/components/Modals/ContactModal.vue'
import { useDoctypeModal } from '@/composables/doctypeModal'
// import ConvertToDealModal from '@/components/Modals/ConvertToDealModal.vue' // disabled: convert-to-deal flow retired
import {
  openWebsite,
  setupCustomizations,
  copyToClipboard,
  validateIsImageFile,
  isTranslatable,
} from '@/utils'
import { getView } from '@/utils/view'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { sessionStore } from '@/stores/session'
import { getMeta } from '@/stores/meta'
import { useDocument } from '@/data/document'
import {
  whatsappEnabled,
  callEnabled,
  aisensyEnabled,
} from '@/composables/settings'
import {
  createResource,
  FileUploader,
  Dropdown,
  Tooltip,
  Avatar,
  Badge,
  Tabs,
  Breadcrumbs,
  call,
  usePageMeta,
  toast,
} from 'frappe-ui'
import { ref, computed, watch, h, nextTick, onMounted, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useActiveTabManager } from '@/composables/useActiveTabManager'

const { brand } = getSettings()
const { $dialog, $socket, makeCall } = globalStore()
const {
  statusOptions,
  getLeadStatus,
  engagementStatusOptions,
  getLeadEngagementStatus,
} = statusesStore()
const { doctypeMeta } = getMeta('CRM Lead')

const route = useRoute()
const router = useRouter()

const props = defineProps({
  leadId: { type: String, required: true },
})

const reload = ref(false)
const activities = ref(null)
const errorTitle = ref('')
const errorMessage = ref('')
const showDeleteLinkedDocModal = ref(false)
// const showConvertToDealModal = ref(false) // disabled: convert-to-deal flow retired
const showFilesUploader = ref(false)

const {
  triggerOnChange,
  triggerOnRender,
  assignees,
  permissions,
  document,
  scripts,
  error,
} = useDocument('CRM Lead', props.leadId)

const canDelete = computed(
  () => (permissions.data?.permissions?.delete || false) && isAdmin(),
)

const doc = computed(() => document.doc || {})

onMounted(async () => {
  if (document.doc) await triggerOnRender()
})

watch(error, (err) => {
  if (err) {
    errorTitle.value = __(
      err.exc_type == 'DoesNotExistError'
        ? 'Document not found'
        : 'Error occurred',
    )
    errorMessage.value = __(err.messages?.[0] || 'An error occurred')
  } else {
    errorTitle.value = ''
    errorMessage.value = ''
  }
})

watch(
  () => document.doc,
  async (_doc) => {
    if (scripts.data?.length) {
      let s = await setupCustomizations(scripts.data, {
        doc: _doc,
        $dialog,
        $socket,
        router,
        toast,
        updateField,
        createToast: toast.create,
        deleteDoc: deleteLead,
        call,
      })
      document._actions = s.actions || []
      document._statuses = s.statuses || []
    }
  },
  { once: true },
)

const breadcrumbs = computed(() => {
  let items = [{ label: __('Leads'), route: { name: 'Leads' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Lead')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Leads',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: { name: 'Lead', params: { leadId: props.leadId } },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return doc.value?.[t] || props.leadId
})

const statuses = computed(() => {
  let customStatuses = document.statuses?.length
    ? document.statuses
    : document._statuses || []
  return statusOptions('lead', customStatuses, triggerStatusChange)
})

const engagementStatuses = computed(() =>
  engagementStatusOptions(triggerLeadStatusChange),
)

usePageMeta(() => {
  return { title: title.value, icon: brand.favicon }
})

const tabs = computed(() => {
  let tabOptions = [
    {
      name: 'Activity',
      label: __('Activity'),
      icon: ActivityIcon,
    },
    {
      name: 'Emails',
      label: __('Emails'),
      icon: EmailIcon,
    },
    {
      name: 'Comments',
      label: __('Comments'),
      icon: CommentIcon,
    },
    {
      name: 'Data',
      label: __('Data'),
      icon: DetailsIcon,
    },
    {
      name: 'Calls',
      label: __('Calls'),
      icon: PhoneIcon,
    },
    {
      name: 'Tasks',
      label: __('Tasks'),
      icon: TaskIcon,
    },
    {
      name: 'Quotes',
      label: __('Quotes'),
      icon: DocumentIcon,
    },
    {
      name: 'Notes',
      label: __('Notes'),
      icon: NoteIcon,
    },
    {
      name: 'Attachments',
      label: __('Attachments'),
      icon: AttachmentIcon,
    },
    {
      name: 'WhatsApp',
      label: __('WhatsApp'),
      icon: WhatsAppIcon,
      condition: () => whatsappEnabled.value,
    },
    {
      name: 'AISensy',
      label: __('WhatsApp (AISensy)'),
      icon: WhatsAppIcon,
      condition: () => aisensyEnabled.value,
    },
  ]
  return tabOptions.filter((tab) => (tab.condition ? tab.condition() : true))
})

const { tabIndex, changeTabTo } = useActiveTabManager(tabs, 'lastLeadTab')

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Lead', 'v2-engagement'],
  params: { doctype: 'CRM Lead' },
  auto: true,
  transform: (data) => getParsedSections(data),
})

function getParsedSections(_sections) {
  if (!Array.isArray(_sections)) return _sections
  return _sections.map((section) => {
    if (!Array.isArray(section?.columns)) return section
    section.columns = section.columns.map((column) => {
      if (!Array.isArray(column?.fields)) return column
      column.fields = column.fields.map((field) => {
        if (field?.fieldname === 'address') {
          return {
            ...field,
            create: (value, close) => {
              showAddressModal()
              close()
            },
            edit: (address) => showAddressModal(address),
          }
        }
        return field
      })
      return column
    })
    return section
  })
}

const { showModal: showDoctypeModal } = useDoctypeModal()

function showAddressModal(_address) {
  showDoctypeModal({
    name: _address || null,
    doctype: 'Address',
    defaults: { address_type: 'Billing' },
    callbacks: {
      afterInsert: (d) => {
        document.doc.address = d.name
        document.save.submit()
      },
    },
  })
}

const leadContacts = createResource({
  url: 'crm.fcrm.doctype.crm_lead.crm_lead.get_lead_contacts',
  cache: ['lead_contacts', props.leadId],
  params: { name: props.leadId },
  transform: (data) => {
    data.forEach((contact) => {
      contact.opened = false
    })
    return data
  },
})

if (!leadContacts.data) leadContacts.fetch()

const showContactModal = ref(false)
let _contact = ref({})

function contactOptions(contact) {
  let options = [
    {
      label: __('Remove'),
      icon: 'trash-2',
      onClick: () => removeContact(contact.name),
    },
  ]
  if (!contact.is_primary) {
    options.push({
      label: __('Set as Primary Contact'),
      icon: h(SuccessIcon, { class: 'h-4 w-4' }),
      onClick: () => setPrimaryContact(contact.name),
    })
  }
  return options
}

async function addContact(contact) {
  if (leadContacts.data?.find((c) => c.name === contact)) {
    toast.error(__('Contact Already Added'))
    return
  }
  let d = await call('crm.fcrm.doctype.crm_lead.crm_lead.add_contact', {
    lead: props.leadId,
    contact,
  })
  if (d) {
    leadContacts.reload()
    toast.success(__('Contact Added'))
  }
}

async function removeContact(contact) {
  let d = await call('crm.fcrm.doctype.crm_lead.crm_lead.remove_contact', {
    lead: props.leadId,
    contact,
  })
  if (d) {
    leadContacts.reload()
    toast.success(__('Contact Removed'))
  }
}

async function setPrimaryContact(contact) {
  let d = await call('crm.fcrm.doctype.crm_lead.crm_lead.set_primary_contact', {
    lead: props.leadId,
    contact,
  })
  if (d) {
    leadContacts.reload()
    toast.success(__('Primary Contact Set'))
  }
}

// --- Manual Create Project handoff (C-stage restructure 2026-05-19) ---
const { isManager, isAdmin } = usersStore()
const _session = sessionStore()

const canShowCreateProject = computed(() => {
  if (!doc.value) return false
  if (doc.value.status !== 'C4') return false
  if (doc.value.lead_status === 'Won') return false
  if (doc.value.custom_external_project_id) return false
  // Owner OR manager (incl. System Manager / Administrator via isManager).
  // Visible for ALL lead types (Retail + Projects) — the downstream project
  // API derives the order type from custom_lead_type and adapts payloads.
  return doc.value.lead_owner === _session.user || isManager()
})

const projectFieldsReady = computed(() => {
  if (!doc.value) return false
  // Retail leads have no project-specific fields to fill; they're ready as
  // soon as the C4 baseline (validate_won_fields + validate_c4_handoff_fields)
  // is satisfied, which is enforced at the status change.
  if (doc.value.custom_lead_type !== 'Projects') return true
  // Project Category + Project Configuration are optional at handoff
  // (sales can fill later). Only site address + pincode are required —
  // OpsGate needs them to geo-tag the project. Mirrors backend gate in
  // CRMLead.validate_project_specific_fields.
  return !!(doc.value.custom_site_address_full && doc.value.custom_site_pincode)
})

const createProjectResource = createResource({
  url: 'crm.api.projects.create_project_for_lead',
  onSuccess: (projectId) => {
    toast.success(__('Project created: {0}', [projectId || __('(pending id)')]))
    reload.value = true
  },
  onError: (err) => {
    toast.error(
      err?.messages?.[0] || err?.message || __('Could not create project.'),
    )
  },
})

function triggerCreateProject() {
  if (
    !window.confirm(
      __(
        'Create the project for this lead? The lead will be marked Won once creation succeeds.',
      ),
    )
  ) {
    return
  }
  createProjectResource.submit({ lead: props.leadId })
}

// Lock when lead is at C4 (Won) AND lead_status == Won.
// Server validators in crm_lead.py (`_enforce_c4_won_lock`) and the
// "CRM Task — Validate — Write Permission" server script enforce the same
// rule on the backend. This computed value drives the UI affordances.
const isLocked = computed(
  () => doc.value?.status === 'C4' && doc.value?.lead_status === 'Won',
)

function bailIfLocked() {
  if (!isLocked.value) return false
  toast.error(__('Lead is Won (C4 + Won). Contact a System Manager to unlock.'))
  return true
}

async function triggerStatusChange(value) {
  if (bailIfLocked()) return
  await triggerOnChange('status', value)
  if (value === 'C7') {
    showFabricatorRoutingReasonModal.value = true
    return
  }
  setLostReason()
}

async function triggerLeadStatusChange(value) {
  if (bailIfLocked()) return
  if (
    value === 'Archived' &&
    !window.confirm(
      __(`Archive lead? C-stage will stay at ${doc.value?.status ?? ''}.`),
    )
  ) {
    return
  }
  await triggerOnChange('lead_status', value)
  document.save.submit(null, {
    onSuccess: () => reloadResources({ lead_status: value }),
  })
}

function updateField(name, value) {
  if (bailIfLocked()) return
  value = Array.isArray(name) ? '' : value
  let oldValues = Array.isArray(name) ? {} : doc.value[name]

  if (Array.isArray(name)) {
    name.forEach((field) => (doc.value[field] = value))
  } else {
    doc.value[name] = value
  }

  document.save.submit(null, {
    onSuccess: () => (reload.value = true),
    onError: (err) => {
      if (Array.isArray(name)) {
        name.forEach((field) => (doc.value[field] = oldValues[field]))
      } else {
        doc.value[name] = oldValues
      }
      toast.error(err.messages?.[0] || __('Error updating field'))
    },
  })
}

function deleteLead() {
  showDeleteLinkedDocModal.value = true
}

function openEmailBox() {
  let currentTab = tabs.value[tabIndex.value]
  if (!['Emails', 'Comments', 'Activities'].includes(currentTab.name)) {
    activities.value.changeTabTo('emails')
  }
  nextTick(() => (activities.value.emailBox.show = true))
}

function statusLabel(status) {
  if (isTranslatable('CRM Lead Status')) return __(status)
  return status
}

const showLostReasonModal = ref(false)
const showFabricatorRoutingReasonModal = ref(false)

function setLostReason() {
  if (
    getLeadStatus(document.doc.status).type !== 'Lost' ||
    (document.doc.lost_reason && document.doc.lost_reason !== 'Other') ||
    (document.doc.lost_reason === 'Other' && document.doc.lost_notes)
  ) {
    document.save.submit(null, {
      onSuccess: () => {
        sections.reload()
        activities.value?.all_activities?.reload()
        activities.value?.quoteRequests?.reload()
      },
    })
    return
  }

  showLostReasonModal.value = true
}

function beforeStatusChange(data) {
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getLeadStatus(data.status).type == 'Lost'
  ) {
    setLostReason()
  } else if (Object.hasOwn(data ?? {}, 'status') && data.status === 'C7') {
    showFabricatorRoutingReasonModal.value = true
  } else if (
    Object.hasOwn(data ?? {}, 'lead_status') &&
    data.lead_status === 'Archived' &&
    !window.confirm(
      __(`Archive lead? C-stage will stay at ${doc.value?.status ?? ''}.`),
    )
  ) {
    // user cancelled archive — revert the field change locally
    if (document.doc) document.doc.lead_status = doc.value?.lead_status
  } else {
    document.save.submit(null, {
      onSuccess: () => reloadResources(data),
    })
  }
}

function reloadResources(data) {
  if (Object.hasOwn(data ?? {}, 'lead_owner')) {
    assignees.reload()
  }
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getLeadStatus(data.status).type != 'Lost'
  ) {
    sections.reload()
    activities.value?.all_activities?.reload()
    activities.value?.quoteRequests?.reload()
  }
}

// Child modals (Task / QR / Note) save through `AllModals.vue`, where they
// can't reach back into this page's resources to trigger a refresh. Provide a
// single function that pulls every dependent resource — the lead doc itself,
// assignees, side-panel sections, the activities feed, and the quotes list —
// so the UI reflects server-side side effects (e.g. Script 5's db.set_value
// advancing the lead to C3 on Accept) without a manual page reload.
function reloadAfterChildModal() {
  document.reload?.()
  assignees.reload?.()
  sections.reload?.()
  activities.value?.all_activities?.reload?.()
  activities.value?.quoteRequests?.reload?.()
}

provide('reloadAfterChildModal', reloadAfterChildModal)
</script>
