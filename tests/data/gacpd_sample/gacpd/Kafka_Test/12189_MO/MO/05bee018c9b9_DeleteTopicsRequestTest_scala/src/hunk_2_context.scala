     val error = response.errorCounts.asScala.find(_._1 != Errors.NONE)
     assertTrue(error.isEmpty, s"There should be no errors, found ${response.data.responses.asScala}")
 
 
     response.data.responses.forEach { response =>
       validateTopicIsDeleted(response.name())
