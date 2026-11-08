// components/loading-overlay/loading-overlay.js
Component({
  properties: {
    visible: {
      type: Boolean,
      value: false
    },
    text: {
      type: String,
      value: '加载中...'
    },
    subText: {
      type: String,
      value: ''
    }
  }
});
