import { defineStore } from 'pinia'
import { getCurrentInstance } from 'vue'

export const globalStore = defineStore('crm-global', () => {
  const app = getCurrentInstance()
  const { $dialog, $socket } = app.appContext.config.globalProperties

  let callMethod = () => {}

  function setMakeCall(value) {
    callMethod = value
  }

  // context: { reference_doctype, reference_docname } — links the call log to a lead/deal
  function makeCall(number, context) {
    callMethod(number, context)
  }

  return {
    $dialog,
    $socket,
    makeCall,
    setMakeCall,
  }
})
