     val error = response.errorCounts.asScala.find(_._1 != Errors.NONE)
     assertTrue(error.isEmpty, s"There should be no errors, found ${response.data.responses.asScala}")
 
 
     request.data.topicNames.forEach { topic =>
       validateTopicIsDeleted(topic)
