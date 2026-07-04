<template>
  <Dialog v-model="show" :options="{ size: 'xl' }">
    <template #body>
      <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
        <!-- Header -->
        <div class="mb-5 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <h3 class="text-2xl font-semibold leading-6 text-ink-gray-9">
              {{ isNewMode ? __('Request Quote') : __('Quote Request') }}
            </h3>
            <Badge
              v-if="!isNewMode && doc.status"
              :label="__(doc.status)"
              :theme="statusTheme(doc.status)"
              variant="subtle"
            />
          </div>
          <Button variant="ghost" class="w-7" icon="x" @click="show = false" />
        </div>

        <!-- Non-estimation users: status/notes/images TOP then estimation fields -->
        <template v-if="!isEstimationTeam">
          <div v-if="canEditOwner" class="mb-4">
            <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
              {{ __('Status') }}
            </div>
            <FormControl
              type="select"
              v-model="localStatus"
              :options="[
                { label: __('Select action...'), value: '' },
                {
                  label: __('Revision Requested'),
                  value: 'Revision Requested',
                },
                { label: __('Accepted'), value: 'Accepted' },
              ]"
            />
          </div>
          <div class="mb-4">
            <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
              {{ __('Notes from Lead Owner') }}
            </div>
            <textarea
              v-if="isNewMode || canEditOwner"
              v-model="localNotes"
              class="w-full resize-none rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-9 placeholder-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
              :placeholder="__('Add notes for the estimation team...')"
              rows="3"
            />
            <p v-else class="whitespace-pre-wrap text-sm text-ink-gray-7">
              {{ doc.notes || __('No notes') }}
            </p>
          </div>
          <div class="mb-4">
            <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
              {{ __('Images') }}
            </div>
            <div class="flex flex-wrap gap-2">
              <div
                v-for="(img, idx) in localImages"
                :key="idx"
                class="group relative"
              >
                <img
                  :src="img.url"
                  class="size-16 cursor-pointer rounded border border-outline-gray-2 object-cover"
                  @click="openFile(img.url)"
                />
                <button
                  v-if="canEditImages"
                  class="absolute -right-1 -top-1 hidden size-4 items-center justify-center rounded-full bg-ink-gray-7 text-white group-hover:flex"
                  @click.stop="removeImage(idx)"
                >
                  <FeatherIcon name="x" class="size-2.5" />
                </button>
              </div>
              <button
                v-if="canEditImages"
                class="flex size-16 items-center justify-center rounded border border-dashed border-outline-gray-3 bg-surface-gray-1 text-ink-gray-5 hover:bg-surface-gray-2"
                @click="showUploader = true"
              >
                <FeatherIcon name="plus" class="size-5" />
              </button>
              <span
                v-if="!localImages.length && !canEditImages"
                class="text-sm text-ink-gray-4"
                >{{ __('No images') }}</span
              >
            </div>
          </div>
        </template>

        <!-- Estimation Team: prefixed quotation-number input.  Shown only
             while the QR is editable by Estimation Team; the raw
             quote_number field is hidden from FieldLayout in this mode. -->
        <div v-if="canEditEstimation" class="mb-4">
          <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
            {{ __('Quotation Number') }}
          </div>
          <div
            class="flex items-stretch overflow-hidden rounded border border-outline-gray-2 bg-surface-gray-1 focus-within:border-outline-gray-4"
          >
            <span
              class="flex select-none items-center border-r border-outline-gray-2 bg-surface-gray-2 px-3 text-sm font-medium text-ink-gray-7"
            >
              TRA-QT-
            </span>
            <input
              v-model="quoteNumberDigits"
              type="text"
              inputmode="numeric"
              autocomplete="off"
              class="w-full bg-transparent px-3 py-1.5 text-sm text-ink-gray-9 placeholder-ink-gray-4 focus:outline-none"
              :placeholder="__('e.g. 12345')"
            />
          </div>
          <p class="mt-1 text-xs text-ink-gray-5">
            {{ __('Paste only the number — the TRA-QT- prefix is added automatically.') }}
          </p>
        </div>

        <!-- Single layout for all modes -->
        <FieldLayout
          v-if="computedTabs"
          :tabs="computedTabs"
          :data="doc"
          doctype="CRM Quote Request"
        />

        <!-- Estimation Team: notes/images BOTTOM (always read-only) -->
        <template v-if="isEstimationTeam">
          <div class="mt-4">
            <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
              {{ __('Notes from Lead Owner') }}
            </div>
            <p class="whitespace-pre-wrap text-sm text-ink-gray-7">
              {{ doc.notes || __('No notes') }}
            </p>
          </div>
          <div class="mt-4">
            <div class="mb-1.5 text-sm font-medium text-ink-gray-5">
              {{ __('Images') }}
            </div>
            <div class="flex flex-wrap gap-2">
              <div v-for="(img, idx) in localImages" :key="idx">
                <img
                  :src="img.url"
                  class="size-16 cursor-pointer rounded border border-outline-gray-2 object-cover"
                  @click="openFile(img.url)"
                />
              </div>
              <span
                v-if="!localImages.length"
                class="text-sm text-ink-gray-4"
                >{{ __('No images') }}</span
              >
            </div>
          </div>
        </template>

        <FilesUploader
          v-if="showUploader && (props.qrName || isNewMode)"
          v-model="showUploader"
          :doctype="isNewMode ? 'CRM Lead' : 'CRM Quote Request'"
          :docname="isNewMode ? props.leadName : props.qrName"
          :options="{
            folder: 'Home/Attachments',
            restrictions: { allowedFileTypes: ['image/*'] },
          }"
          @after="onImagesUploaded"
        />

        <ErrorMessage v-if="error" class="mt-4" :message="__(error)" />
      </div>

      <!-- Footer -->
      <div class="px-4 pb-7 pt-4 sm:px-6">
        <div class="flex flex-row-reverse gap-2">
          <template v-if="isNewMode">
            <Button
              variant="solid"
              :label="__('Request Quote')"
              :loading="saving"
              @click="submitNewQuote"
            />
          </template>
          <template v-else-if="canEditOwner">
            <Button
              variant="solid"
              :label="__('Save')"
              :loading="saving"
              @click="saveOwnerChanges"
            />
          </template>
          <template v-else-if="canEditEstimation">
            <Button
              variant="solid"
              :label="__('Submit Quote')"
              :loading="saving"
              @click="submitEstimation"
            />
          </template>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import FieldLayout from '@/components/FieldLayout/FieldLayout.vue'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import { useDocument } from '@/data/document'
import { sessionStore } from '@/stores/session'
import { usersStore } from '@/stores/users'
import {
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  FeatherIcon,
  FormControl,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { computed, onMounted, ref, watch } from 'vue'

// ─── props / emits ────────────────────────────────────────────────────────────
const props = defineProps({
  qrName: { type: String, default: '' },
  leadName: { type: String, default: '' },
  leadDoc: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['updated', 'success'])
const show = defineModel({ type: Boolean })

// ─── stores ──────────────────────────────────────────────────────────────────
const { user: currentUser } = sessionStore()
const { getUserRole, isReviewer } = usersStore()

// ─── mode ────────────────────────────────────────────────────────────────────
const isNewMode = computed(() => !props.qrName)

// ─── document state ───────────────────────────────────────────────────────────
const { document: qrDoc } = useDocument(
  'CRM Quote Request',
  props.qrName || null,
)
const doc = computed(() => qrDoc.doc || {})

// ─── permissions ──────────────────────────────────────────────────────────────
const isEstimationTeam = computed(
  () => getUserRole(currentUser) === 'Estimation Team',
)
const isLeadOwner = computed(
  () => !isNewMode.value && doc.value.lead_owner === currentUser,
)
const canReview = computed(
  () => !isNewMode.value && (isLeadOwner.value || isReviewer(currentUser)),
)

const EDITABLE_ESTIMATION_STATUSES = ['Pending', 'Revision Requested']

const canEditEstimation = computed(
  () =>
    !isNewMode.value &&
    isEstimationTeam.value &&
    EDITABLE_ESTIMATION_STATUSES.includes(doc.value.status),
)
const canEditOwner = computed(
  () =>
    !isNewMode.value &&
    canReview.value &&
    doc.value.status === 'Quote Received',
)
const canEditImages = computed(() => isNewMode.value || canEditOwner.value)

// ─── fields layout ────────────────────────────────────────────────────────────
const layout = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_fields_layout',
  cache: ['QuickEntry', 'CRM Quote Request'],
  params: { doctype: 'CRM Quote Request', type: 'Quick Entry' },
  auto: true,
})

const ESTIMATION_FIELDS = [
  'quote_file',
  'quote_value',
  'quote_margin',
  'quote_sq_ft',
  'total_quantity',
  'quote_number',
  'quote_validity',
  'estimation_remarks',
  'prepared_by',
]
const ALWAYS_READONLY = ['lead', 'requested_by', 'requested_on']

function applyFieldProps(field) {
  if (
    field.fieldname === 'notes' ||
    field.fieldname === 'images' ||
    field.fieldname === 'status' ||
    (field.fieldname === 'prepared_by' && isNewMode.value)
  ) {
    field.hidden = 1
    return
  }
  // When the Estimation Team is editing, hide the raw quote_number field —
  // we render our own prefixed input above the layout so the OpsGate-required
  // `TRA-QT-` prefix is visible and can't be omitted.
  if (field.fieldname === 'quote_number' && canEditEstimation.value) {
    field.hidden = 1
    return
  }
  if (ALWAYS_READONLY.includes(field.fieldname)) {
    field.read_only = 1
    return
  }
  if (ESTIMATION_FIELDS.includes(field.fieldname)) {
    field.read_only = canEditEstimation.value ? 0 : 1
  } else {
    field.read_only = 1
  }
}

function processTab(tab) {
  tab.sections.forEach((section) => {
    if (section.name === 'quote_notes_section') section.label = ''
    section.columns.forEach((column) => {
      column.fields.forEach(applyFieldProps)
      column.fields = column.fields.filter((f) => !f.hidden)
    })
  })
}

// Single computed for all modes
const computedTabs = computed(() => {
  if (!layout.data) return null
  const tabs = JSON.parse(JSON.stringify(layout.data))
  tabs.forEach(processTab)
  return tabs
})

// ─── quote number prefix ─────────────────────────────────────────────────────
// OpsGate rejects quote numbers that don't match `TRA-QT-<digits>`, so we keep
// the `TRA-QT-` prefix static in the UI and only capture the digits from the
// Estimation Team.  On load we strip whatever prefix variant is stored so the
// input shows just the numeric tail; on save we recombine (submitEstimation).
const QUOTE_NUMBER_PREFIX = 'TRA-QT-'
const quoteNumberDigits = ref('')

function stripQuotePrefix(value) {
  const raw = (value || '').trim()
  if (!raw) return ''
  const upper = raw.toUpperCase()
  if (upper.startsWith('TRA-QT-')) return raw.slice('TRA-QT-'.length).trim()
  if (upper.startsWith('TRA-QT')) return raw.slice('TRA-QT'.length).replace(/^-/, '').trim()
  return raw
}

watch(
  () => qrDoc.doc?.quote_number,
  (v) => {
    quoteNumberDigits.value = stripQuotePrefix(v)
  },
  { immediate: true },
)

// ─── local status (lead owner pick — don't mutate qrDoc.doc.status directly) ─
const localStatus = ref('')

watch(
  () => qrDoc.doc?.status,
  (v) => {
    if (v === 'Quote Received') localStatus.value = ''
  },
  { immediate: true },
)

// ─── notes ────────────────────────────────────────────────────────────────────
const localNotes = ref('')
const notesInited = ref(false)

watch(
  () => qrDoc.doc?.notes,
  (v) => {
    if (!notesInited.value && v !== undefined) {
      localNotes.value = v || ''
      notesInited.value = true
    }
  },
  { immediate: true },
)

// ─── images ───────────────────────────────────────────────────────────────────
const localImages = ref([])
const imagesInited = ref(false)
const showUploader = ref(false)

watch(
  () => qrDoc.doc?.images,
  (rows) => {
    if (!imagesInited.value && rows !== undefined) {
      localImages.value = rows?.length
        ? rows.map((r) => ({ url: r.image, name: r.name }))
        : []
      imagesInited.value = true
    }
  },
  { immediate: true },
)

function onImagesUploaded(files) {
  files.forEach((f) => localImages.value.push({ url: f.file_url }))
}

function removeImage(idx) {
  localImages.value.splice(idx, 1)
}

function openFile(url) {
  window.open(url, '_blank')
}

// ─── save state ───────────────────────────────────────────────────────────────
const saving = ref(false)
const error = ref(null)

// ─── helpers ──────────────────────────────────────────────────────────────────
function extractFrappeError(err) {
  if (err?.messages?.length) return err.messages[0]
  return err?.message || __('An error occurred.')
}

function statusTheme(status) {
  return (
    {
      Pending: 'orange',
      'Quote Received': 'blue',
      'Revision Requested': 'yellow',
      Accepted: 'green',
    }[status] || 'gray'
  )
}

// ─── new mode ─────────────────────────────────────────────────────────────────
async function submitNewQuote() {
  saving.value = true
  error.value = null
  try {
    const imageUrls = localImages.value.map((i) => i.url)
    const qrName = await call('crm.api.quotes.request_quote', {
      lead: props.leadName,
      notes: localNotes.value,
      images: imageUrls,
    })
    toast.success(__('Quote Request created'))
    emit('success', qrName)
    show.value = false
  } catch (err) {
    error.value = extractFrappeError(err)
  } finally {
    saving.value = false
  }
}

// ─── estimation team save ─────────────────────────────────────────────────────
async function submitEstimation() {
  if (!doc.value.quote_file) {
    error.value = __('Please upload the quote file before submitting.')
    return
  }
  const digits = quoteNumberDigits.value.trim()
  if (!digits) {
    error.value = __('Enter the quotation number.')
    return
  }
  // Recombine prefix + digits so the server receives the canonical
  // `TRA-QT-<digits>` string.  Backend normalization is still the safety net.
  qrDoc.doc.quote_number = QUOTE_NUMBER_PREFIX + digits
  saving.value = true
  error.value = null
  try {
    await call('frappe.client.save', { doc: qrDoc.doc })
    toast.success(__('Quote submitted'))
    emit('updated')
    show.value = false
  } catch (err) {
    error.value = extractFrappeError(err)
  } finally {
    saving.value = false
  }
}

// ─── lead owner save ──────────────────────────────────────────────────────────
async function saveOwnerChanges() {
  if (!localStatus.value) {
    error.value = __('Please select a status.')
    return
  }
  if (localStatus.value === 'Revision Requested' && !localNotes.value?.trim()) {
    error.value = __('Add notes explaining what revision is needed.')
    return
  }
  saving.value = true
  error.value = null
  try {
    await call('frappe.client.save', {
      doc: {
        ...qrDoc.doc,
        status: localStatus.value,
        notes: localNotes.value,
        images: localImages.value.map((r) => ({
          ...(r.name ? { name: r.name } : {}),
          image: r.url,
          uploaded_by: currentUser,
        })),
      },
    })
    toast.success(__('Saved'))
    emit('updated')
    show.value = false
  } catch (err) {
    error.value = extractFrappeError(err)
  } finally {
    saving.value = false
  }
}

// ─── init ─────────────────────────────────────────────────────────────────────
onMounted(() => {
  error.value = null
  if (isNewMode.value) {
    notesInited.value = true
    imagesInited.value = true
    qrDoc.doc = {
      __newDocument: true,
      doctype: 'CRM Quote Request',
      notes: '',
      lead: props.leadName,
      quote_value: props.leadDoc?.custom_tentative_value || 0,
      quote_sq_ft: props.leadDoc?.custom_tentative_area_sqft || 0,
      quote_margin: props.leadDoc?.custom_final_margin || 0,
      total_quantity: props.leadDoc?.custom_total_quantity || 0,
      quote_number: '',
      quote_validity: '',
    }
    localNotes.value = ''
    localImages.value = []
  }
})
</script>
