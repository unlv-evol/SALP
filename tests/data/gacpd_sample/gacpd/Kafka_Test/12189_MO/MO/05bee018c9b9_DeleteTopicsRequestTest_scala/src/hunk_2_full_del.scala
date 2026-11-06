@@ -85,9 +83,7 @@ class DeleteTopicsRequestTest extends BaseRequestTest with Logging {
     val error = response.errorCounts.asScala.find(_._1 != Errors.NONE)
     assertTrue(error.isEmpty, s"There should be no errors, found ${response.data.responses.asScala}")
 
-    if (isKRaftTest()) {
-      TestUtils.ensureConsistentKRaftMetadata(brokers, controllerServer)
-    }
 
     response.data.responses.forEach { response =>
       validateTopicIsDeleted(response.name())
